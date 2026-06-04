"""Telegram publisher — sends posts, manages schedule, handles broadcasts."""

import asyncio
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from verdikt_auto.core.config import Settings
from verdikt_auto.core.exceptions import PublisherError
from verdikt_auto.core.models import Post

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = [2.0, 5.0, 15.0]
BOT_TOKEN_PLACEHOLDER = "BOT_TOKEN_PLACEHOLDER"


class TelegramPublisher:
    """Publishes posts to Telegram channel with retry logic."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram.telegram_bot_token
        self._channel_id = settings.telegram.telegram_channel_id
        self._admin_chat_id = settings.telegram.admin_chat_id
        self._bot: Optional[object] = None

    async def _get_bot(self) -> object:
        if self._bot is None:
            from telegram import Bot
            self._bot = Bot(token=self._token)
        return self._bot

    async def publish_post(self, post: Post) -> Optional[int]:
        text = self._build_caption(post)

        for attempt in range(MAX_RETRIES):
            try:
                bot = await self._get_bot()
                msg = await bot.send_message(
                    chat_id=self._channel_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
                logger.info("Post published: id=%s, msg_id=%s", post.id[:8], msg.message_id)
                return msg.message_id
            except Exception as exc:
                logger.warning("Publish attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])

        logger.error("All publish attempts failed for post %s", post.id[:8])
        return None

    async def publish_with_photo(self, post: Post, photo_path: str) -> Optional[int]:
        text = self._build_caption(post)
        for attempt in range(MAX_RETRIES):
            try:
                bot = await self._get_bot()
                with open(photo_path, "rb") as f:
                    msg = await bot.send_photo(
                        chat_id=self._channel_id,
                        photo=f,
                        caption=text,
                        parse_mode="HTML",
                    )
                logger.info("Post with photo published: msg_id=%s", msg.message_id)
                return msg.message_id
            except Exception as exc:
                logger.warning("Photo publish attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None

    async def publish_with_voice(self, post: Post, voice_path: str) -> Optional[int]:
        text = f"🎧 <b>{post.headline}</b>" if post.headline else "🎧 Аудиоверсия"
        for attempt in range(MAX_RETRIES):
            try:
                bot = await self._get_bot()
                with open(voice_path, "rb") as f:
                    msg = await bot.send_voice(
                        chat_id=self._channel_id,
                        voice=f,
                        caption=text,
                        parse_mode="HTML",
                    )
                logger.info("Voice published: msg_id=%s", msg.message_id)
                return msg.message_id
            except Exception as exc:
                logger.warning("Voice publish attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None

    async def notify_admin(self, message: str) -> bool:
        if not self._admin_chat_id:
            return False
        try:
            bot = await self._get_bot()
            await bot.send_message(
                chat_id=self._admin_chat_id,
                text=f"🤖 <b>VERDIKT-AUTO</b>\n{message}",
                parse_mode="HTML",
            )
            return True
        except Exception as exc:
            logger.warning("Admin notification failed: %s", exc)
            return False

    def _build_caption(self, post: Post) -> str:
        from verdikt_auto.formatter.text_formatter import TextFormatter
        return TextFormatter().format_post(post)


class PostScheduler:
    """Manages posting schedule with dynamic adjustment."""

    def __init__(
        self,
        publisher: TelegramPublisher,
        schedule_hours: Optional[list[int]] = None,
        posts_per_day: int = 7,
    ) -> None:
        self.publisher = publisher
        self._schedule_hours = schedule_hours or [7, 9, 12, 14, 18, 20, 22]
        self._posts_per_day = posts_per_day
        self._queue: asyncio.Queue[Post] = asyncio.Queue()
        self._running = False
        self._post_fn: Optional[Callable[[Post], Any]] = None

    def set_post_fn(self, fn: Callable[[Post], Any]) -> None:
        self._post_fn = fn

    async def enqueue(self, post: Post) -> None:
        await self._queue.put(post)

    async def adjust_schedule(self, analytics: dict[str, float]) -> None:
        peak_hours = [h for h in self._schedule_hours if 18 <= h <= 23]
        if analytics.get("evening_engagement", 0.5) > 0.7 and peak_hours:
            extra_hour = random.choice([h for h in range(19, 23) if h not in self._schedule_hours])
            if extra_hour:
                self._schedule_hours.append(extra_hour)
                self._schedule_hours.sort()
                logger.info("Schedule adjusted: added hour %d", extra_hour)

    async def run_loop(self) -> None:
        self._running = True
        logger.info("PostScheduler started: %d posts/day at %s", self._posts_per_day, self._schedule_hours)

        while self._running:
            now = datetime.now()
            next_hour = self._next_hour(now)

            if next_hour:
                wait = (next_hour - now).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)

            posts_to_publish = min(self._posts_per_day, max(1, self._queue.qsize()))
            for _ in range(posts_to_publish):
                try:
                    post = await asyncio.wait_for(self._queue.get(), timeout=30)
                    if self._post_fn:
                        await self._post_fn(post)
                    else:
                        await self.publisher.publish_post(post)
                except asyncio.TimeoutError:
                    break
                except Exception as exc:
                    logger.error("Post processing failed: %s", exc)

            if post := None:
                pass
            await asyncio.sleep(60)

    def _next_hour(self, now: datetime) -> Optional[datetime]:
        today = now.replace(minute=0, second=0, microsecond=0)
        for h in self._schedule_hours:
            candidate = today.replace(hour=h)
            if candidate > now:
                return candidate
        return today.replace(hour=self._schedule_hours[0]) + timedelta(days=1)

    def stop(self) -> None:
        self._running = False


class BroadcastManager:
    """Handles welcome broadcasts, weekly digests, and urgent verdicts."""

    def __init__(self, publisher: TelegramPublisher) -> None:
        self.publisher = publisher

    async def send_welcome(self, chat_id: str) -> bool:
        text = (
            "👋 <b>Добро пожаловать на канал VERDIKT!</b>\n\n"
            "Каждый день — 7 постов с аналитикой главных новостей:\n"
            "🔍 Суть события\n"
            "📊 Контекст и цифры\n"
            "💡 Прогноз и выводы\n\n"
            "Подпишись, чтобы не пропустить!"
        )
        try:
            bot = await self._get_bot()
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            logger.info("Welcome sent to %s", chat_id)
            return True
        except Exception as exc:
            logger.error("Welcome failed: %s", exc)
            return False

    async def send_weekly_digest(self, posts: list[Post]) -> bool:
        lines = ["📊 <b>Еженедельный дайджест VERDIKT</b>\n"]
        for i, post in enumerate(posts[:10], 1):
            headline = post.headline or "Без заголовка"
            lines.append(f"{i}. <b>{headline}</b>")
            if post.tags:
                lines.append(f"   {' '.join(f'#{t}' for t in post.tags[:3])}")
            lines.append("")

        text = "\n".join(lines)
        return await self.publisher.notify_admin(text)

    async def send_urgent_verdict(self, post: Post) -> Optional[int]:
        text = (
            "⚡ <b>СРОЧНЫЙ ВЕРДИКТ</b>\n\n"
            f"<b>{post.headline}</b>\n\n"
            f"{post.content.text[:500] if post.content else ''}\n\n"
            f"#срочно #вердикт"
        )
        try:
            bot = await self._get_bot()
            msg = await bot.send_message(
                chat_id=self._get_channel_id(),
                text=text,
                parse_mode="HTML",
            )
            logger.info("Urgent verdict published: msg_id=%s", msg.message_id)
            return msg.message_id
        except Exception as exc:
            logger.error("Urgent verdict failed: %s", exc)
            return None

    async def _get_bot(self) -> object:
        from telegram import Bot
        return Bot(token=BOT_TOKEN_PLACEHOLDER)

    def _get_channel_id(self) -> str:
        return "@verdict_channel"
