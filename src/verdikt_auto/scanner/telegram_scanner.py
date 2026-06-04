"""Telegram scanner via Telethon userbot."""

import logging
import re
from datetime import datetime
from typing import Any, Optional

from verdikt_auto.scanner.base_scanner import BaseScanner, ScannerConfig
from verdikt_auto.scanner.models import Topic

logger = logging.getLogger(__name__)


class TelegramScanner(BaseScanner):
    """Scan public Telegram channels via MTProto (Telethon)."""

    def __init__(self, session_path: str = "data/telethon.session", config: Optional[ScannerConfig] = None) -> None:
        super().__init__(name="telegram", config=config, rpm=10)
        self.session_path = session_path
        self._channels: list[str] = []
        self._client = None

    def configure(self, channels: list[str]) -> None:
        self._channels = channels

    async def health_check(self) -> bool:
        return len(self._channels) > 0

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from telethon import TelegramClient
            self._client = TelegramClient(self.session_path, 0, None)
            return self._client
        except ImportError:
            logger.error("telethon not installed (pip install telethon)")
            return None
        except Exception as exc:
            logger.error("Telegram client init error: %s", exc)
            return None

    async def _fetch_channel_posts(self, channel: str) -> list[Topic]:
        """Fetch last 20 posts from a channel."""
        client = self._get_client()
        if client is None:
            return []

        topics: list[Topic] = []
        try:
            await self._rate_limit()

            if not client.is_connected():
                await client.connect()

            entity = await client.get_entity(channel)
            messages = await client.get_messages(entity, limit=20)

            for msg in messages:
                if not msg.text:
                    continue

                text = msg.text[:500]
                views = getattr(msg, "views", 0) or 0
                forwards = getattr(msg, "forwards", 0) or 0

                reactions_count = 0
                if hasattr(msg, "reactions") and msg.reactions:
                    reactions_count = sum(
                        r.count for r in msg.reactions.results
                    ) if hasattr(msg.reactions, "results") and msg.reactions.results else 0

                keywords = self._extract_keywords(text)
                title = text.split("\n")[0][:120]

                topic = Topic.from_raw(
                    title=title,
                    source="telegram",
                    source_name=channel,
                    url=f"https://t.me/{channel.strip('@')}/{msg.id}",
                    published_at=msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
                    views=views,
                    likes=reactions_count,
                    comments=0,
                    shares=forwards,
                    keywords=keywords,
                    raw_data={
                        "channel": channel,
                        "message_id": msg.id,
                        "text_preview": text[:200],
                    },
                )
                topics.append(topic)

            self._total_scanned += len(topics)
            logger.info("Telegram scanned %d posts from %s", len(topics), channel)

        except Exception as exc:
            self._total_errors += 1
            logger.error("Telegram scan error for %s: %s", channel, exc)

        return topics

    async def scan(self) -> list[Topic]:
        all_topics: list[Topic] = []
        for channel in self._channels:
            posts = await self._fetch_channel_posts(channel)
            all_topics.extend(posts)
        logger.info("Telegram scan complete: %d topics from %d channels", len(all_topics), len(self._channels))
        return all_topics

    def _extract_keywords(self, text: str) -> list[str]:
        words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{4,}", text.lower())
        stop_words = {
            "это", "что", "как", "для", "все", "так", "еще", "когда", "уже",
            "the", "and", "for", "are", "not", "its", "was",
        }
        return [w for w in words if w not in stop_words][:10]

    async def close(self) -> None:
        if self._client and self._client.is_connected():
            await self._client.disconnect()
