"""Auto Commentator — guerrilla-style commenting via AI Router with strict safety limits."""

import logging
import random
from typing import Any, Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import GeneratedContent

logger = logging.getLogger(__name__)

MAX_COMMENTS_PER_DAY = 20
MIN_INTERVAL_MINUTES = 15
MAX_COMMENT_LENGTH = 300


class AutoCommentator:
    """Generates and dispatches contextual comments on related channels."""

    def __init__(self, router: AIRouter) -> None:
        self.router = router
        self._daily_count = 0
        self._last_comment_time: Optional[float] = None
        self._target_channels: list[str] = []

    def set_targets(self, channels: list[str]) -> None:
        self._target_channels = channels
        logger.info("Comment targets set: %d channels", len(channels))

    def can_comment(self) -> bool:
        import time
        if self._daily_count >= MAX_COMMENTS_PER_DAY:
            return False
        if self._last_comment_time:
            elapsed = (time.time() - self._last_comment_time) / 60
            if elapsed < MIN_INTERVAL_MINUTES:
                return False
        return True

    async def generate_comment(self, post_text: str, channel_name: str) -> Optional[str]:
        if not self.can_comment():
            logger.warning("Comment limit reached — skipping")
            return None

        prompt = (
            f"Напиши короткий естественный комментарий к посту в Telegram.\n"
            f"Правила:\n"
            f"- Максимум {MAX_COMMENT_LENGTH} символов\n"
            f"- Выгляди как обычный пользователь\n"
            f"- Не рекламируй канал прямо\n"
            f"- Не используй эмодзи в каждом предложении\n"
            f"- Добавь полезное мнение или вопрос\n"
            f"- Без упоминания конкурентов\n\n"
            f"Канал: {channel_name}\n"
            f"Текст поста:\n{post_text[:1000]}"
        )
        result = await self.router.route("generate_post", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        cleaned = text.strip().strip('"').strip("'")[:MAX_COMMENT_LENGTH]
        return cleaned

    async def dispatch(self, comment: str, channel_id: str) -> bool:
        import time
        try:
            logger.info("Would dispatch to %s: %s...", channel_id, comment[:50])
            self._daily_count += 1
            self._last_comment_time = time.time()
            return True
        except Exception as exc:
            logger.error("Dispatch failed: %s", exc)
            return False

    async def run_daily_round(self, posts_by_channel: dict[str, str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for channel, post_text in posts_by_channel.items():
            if not self.can_comment():
                break
            comment = await self.generate_comment(post_text, channel)
            if comment:
                success = await self.dispatch(comment, channel)
                results.append({"channel": channel, "comment": comment, "success": success})
        logger.info("Daily comment round: %d/%d dispatched", len(results), len(posts_by_channel))
        return results

    def reset_daily(self) -> None:
        self._daily_count = 0
        logger.info("Daily comment counter reset")
