"""RSS scanner using feedparser."""

import logging
from datetime import datetime
from typing import Any, Optional

import httpx

from verdikt_auto.scanner.base_scanner import BaseScanner, ScannerConfig
from verdikt_auto.scanner.models import Topic, TopicCategory

logger = logging.getLogger(__name__)


class RSSScanner(BaseScanner):
    """Scan RSS/Atom feeds for news articles."""

    FEED_CATEGORIES: dict[str, TopicCategory] = {
        "ria.ru": TopicCategory.GENERAL,
        "tass.ru": TopicCategory.GENERAL,
        "rbc.ru": TopicCategory.ECONOMY,
        "lenta.ru": TopicCategory.GENERAL,
        "meduza.io": TopicCategory.GENERAL,
        "habr.com": TopicCategory.TECH,
    }

    def __init__(self, config: Optional[ScannerConfig] = None) -> None:
        super().__init__(name="rss", config=config, rpm=60)
        self._feeds: list[dict] = []

    def configure(self, feeds: list[dict]) -> None:
        self._feeds = feeds

    async def health_check(self) -> bool:
        return len(self._feeds) > 0

    async def _fetch_and_parse(self, feed_url: str) -> list[dict]:
        """Fetch XML and parse with feedparser."""
        await self._rate_limit()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(feed_url, headers={"User-Agent": "verdikt-auto/1.0"})
                resp.raise_for_status()

            import feedparser
            parsed = feedparser.parse(resp.text)
            items: list[dict] = []

            for entry in parsed.entries[:20]:
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    import time
                    published = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    import time
                    published = datetime.fromtimestamp(time.mktime(entry.updated_parsed))

                content = ""
                if hasattr(entry, "content") and entry.content:
                    content = entry.content[0].get("value", "")
                elif hasattr(entry, "summary"):
                    content = entry.summary or ""
                elif hasattr(entry, "description"):
                    content = entry.description or ""

                items.append({
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", ""),
                    "content": content,
                    "published": published,
                })

            return items

        except Exception as exc:
            logger.error("RSS fetch error for %s: %s", feed_url, exc)
            return []

    async def scan(self) -> list[Topic]:
        all_topics: list[Topic] = []
        for feed in self._feeds:
            try:
                feed_url = feed.get("url", "")
                feed_category_str = feed.get("category", "general")

                domain = feed_url.split("/")[2] if "//" in feed_url else ""
                feed_category = self.FEED_CATEGORIES.get(domain, TopicCategory(feed_category_str))

                items = await self._fetch_and_parse(feed_url)

                for item in items:
                    import re
                    clean_text = re.sub(r"<[^>]+>", "", item.get("content", "")).strip()
                    title = item.get("title", "Untitled")

                    topic = Topic.from_raw(
                        title=title,
                        source="rss",
                        source_name=domain or feed_url,
                        url=item.get("link", ""),
                        published_at=item.get("published"),
                        keywords=[feed_category.value],
                        raw_data={
                            "feed_url": feed_url,
                            "content_preview": clean_text[:300],
                        },
                    )
                    # Override category with feed default
                    topic.category = feed_category
                    all_topics.append(topic)

                self._total_scanned += len(items)
                logger.info("RSS scanned %d items from %s", len(items), feed_url)

            except Exception as exc:
                self._total_errors += 1
                logger.error("RSS scan error for %s: %s", feed.get("url"), exc)

        logger.info("RSS scan complete: %d topics", len(all_topics))
        return all_topics
