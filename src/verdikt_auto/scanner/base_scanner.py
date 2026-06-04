"""Abstract base scanner with built-in rate limiting."""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from verdikt_auto.scanner.models import Topic

logger = logging.getLogger(__name__)


class ScannerConfig:
    """Configuration holder for a scanner."""

    def __init__(self, data: Optional[dict[str, Any]] = None) -> None:
        self._data: dict[str, Any] = data or {}

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        val: Any = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default


class BaseScanner(ABC):
    """Abstract scanner that all source scanners inherit from."""

    def __init__(self, name: str, config: Optional[ScannerConfig] = None, rpm: int = 30) -> None:
        self.name = name
        self.config = config or ScannerConfig()
        self._rate_limiter = asyncio.Semaphore(rpm)
        self._last_request: Optional[datetime] = None
        self._min_interval = 60.0 / max(rpm, 1)
        self._total_scanned = 0
        self._total_errors = 0

    @abstractmethod
    async def scan(self) -> list[Topic]:
        ...

    async def health_check(self) -> bool:
        return True

    async def _rate_limit(self) -> None:
        """Enforce minimum interval between requests."""
        if self._last_request is not None:
            elapsed = (datetime.now() - self._last_request).total_seconds()
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = datetime.now()

    def normalize(self, raw: dict) -> Topic:
        """Convert a raw data dict into a Topic. Override in subclass."""
        return Topic.from_raw(
            title=raw.get("title", "Untitled"),
            source=self.name,
            source_name=raw.get("source_name", self.name),
            url=raw.get("url", ""),
            published_at=raw.get("published_at"),
            views=raw.get("views", 0),
            likes=raw.get("likes", 0),
            comments=raw.get("comments", 0),
            shares=raw.get("shares", 0),
            keywords=raw.get("keywords", []),
            raw_data=raw,
        )

    def get_stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_scanned": self._total_scanned,
            "total_errors": self._total_errors,
        }
