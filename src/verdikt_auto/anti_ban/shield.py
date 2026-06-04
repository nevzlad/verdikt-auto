"""Anti-ban shield — rate limiter, shadow ban detector, auto-pause."""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from verdikt_auto.core.models import Post
from verdikt_auto.publisher.telegram_publisher import TelegramPublisher

logger = logging.getLogger(__name__)

MAX_MSG_PER_MINUTE = 20
SHADOW_BAN_REACH_THRESHOLD = 0.10
COOLDOWN_DURATION_SEC = 600


class RateWindow:
    """Sliding window rate counter."""

    def __init__(self, max_count: int, window_sec: float = 60.0) -> None:
        self._max_count = max_count
        self._window_sec = window_sec
        self._timestamps: deque[float] = deque()

    def can_proceed(self) -> bool:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self._window_sec:
            self._timestamps.popleft()
        return len(self._timestamps) < self._max_count

    def record(self) -> None:
        self._timestamps.append(time.monotonic())

    @property
    def count(self) -> int:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self._window_sec:
            self._timestamps.popleft()
        return len(self._timestamps)


class AntiBanShield:
    """Protection layer against Telegram bans and shadow bans."""

    def __init__(self, publisher: Optional[TelegramPublisher] = None) -> None:
        self.publisher = publisher
        self._rate_limiter = RateWindow(MAX_MSG_PER_MINUTE)
        self._subscriber_base: int = 0
        self._expected_reach: float = 0.0
        self._cooldown_until: float = 0.0
        self._shadow_ban_warnings: int = 0
        self._message_stats: deque[dict[str, object]] = deque(maxlen=100)

    async def check_before_publish(self, post: Post) -> bool:
        if self._is_cooldown():
            logger.warning("Shield in cooldown — blocking publish")
            return False

        if not self._rate_limiter.can_proceed():
            logger.warning("Rate limit reached (%d/min) — blocking", self._rate_limiter.count)
            return False

        return True

    async def record_publish(self, post: Post, message_id: Optional[int]) -> None:
        self._rate_limiter.record()
        self._message_stats.append({
            "post_id": post.id,
            "message_id": message_id,
            "timestamp": datetime.now().isoformat(),
        })

    async def check_shadow_ban(self, post: Post, actual_views: int) -> None:
        if self._subscriber_base == 0:
            return

        reach_ratio = actual_views / self._subscriber_base
        if reach_ratio < SHADOW_BAN_REACH_THRESHOLD:
            self._shadow_ban_warnings += 1
            logger.warning(
                "Shadow ban suspect: reach %.1f%% (threshold %.1f%%) — warning %d/3",
                reach_ratio * 100, SHADOW_BAN_REACH_THRESHOLD * 100,
                self._shadow_ban_warnings,
            )

            if self._shadow_ban_warnings >= 3:
                await self._activate_cooldown()
                if self.publisher:
                    await self.publisher.notify_admin(
                        "🚨 <b>Обнаружен теневой бан!</b>\n"
                        f"Охват: {reach_ratio * 100:.1f}% (< {SHADOW_BAN_REACH_THRESHOLD * 100}%)\n"
                        f"Подписчиков: {self._subscriber_base}\n"
                        f"Просмотров: {actual_views}\n"
                        "⏸ Публикация приостановлена на 10 минут"
                    )
        else:
            self._shadow_ban_warnings = max(0, self._shadow_ban_warnings - 1)

    def update_subscriber_base(self, count: int) -> None:
        self._subscriber_base = count
        self._expected_reach = count * 0.30

    def _is_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    async def _activate_cooldown(self) -> None:
        self._cooldown_until = time.monotonic() + COOLDOWN_DURATION_SEC
        logger.info("Cooldown activated for %d seconds", COOLDOWN_DURATION_SEC)

    def get_stats(self) -> dict[str, object]:
        return {
            "rate_current": self._rate_limiter.count,
            "rate_max": MAX_MSG_PER_MINUTE,
            "cooldown_active": self._is_cooldown(),
            "cooldown_remaining": max(0, self._cooldown_until - time.monotonic()),
            "shadow_ban_warnings": self._shadow_ban_warnings,
            "subscriber_base": self._subscriber_base,
            "messages_sent": len(self._message_stats),
        }
