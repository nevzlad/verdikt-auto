"""Topic aggregator — gathers topics from all scanners, deduplicates, enriches."""

import asyncio
import hashlib
import logging
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from verdikt_auto.scanner.base_scanner import BaseScanner
from verdikt_auto.scanner.models import Topic
from verdikt_auto.scanner.topics_db import TopicsDB

logger = logging.getLogger(__name__)


class TopicAggregator:
    """Runs all scanners in parallel, deduplicates, enriches, and stores topics."""

    def __init__(self, scanners: list[BaseScanner], db: Optional[TopicsDB] = None) -> None:
        self.scanners = scanners
        self.db = db or TopicsDB()
        self._sim_threshold = 0.80

    async def collect(self, top_n: int = 20) -> list[Topic]:
        """Run all scanners in parallel, deduplicate, return top-N topics."""
        all_topics: list[Topic] = []

        # Run scanners in parallel, each catching its own errors
        results = await asyncio.gather(
            *[self._safe_scan(s) for s in self.scanners],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.error("Scanner raised exception: %s", result)
                continue
            if isinstance(result, list):
                all_topics.extend(result)

        logger.info("Raw topics collected: %d", len(all_topics))

        # Deduplicate
        unique_topics = self._deduplicate(all_topics)
        logger.info("After deduplication: %d", len(unique_topics))

        # Enrich
        enriched = self._enrich(unique_topics)
        logger.info("Enriched topics: %d", len(enriched))

        # Sort by viral_score
        enriched.sort(key=lambda t: t.viral_score, reverse=True)

        # Save to DB
        if self.db:
            for topic in enriched:
                self.db.add_topic(topic)

        top_results = enriched[:top_n]
        logger.info("Top %d topics: %s", len(top_results), [t.title[:50] for t in top_results])
        return top_results

    async def _safe_scan(self, scanner: BaseScanner) -> list[Topic]:
        """Run a single scanner safely."""
        try:
            return await scanner.scan()
        except Exception as exc:
            logger.exception("Scanner %s crashed: %s", scanner.name, exc)
            return []

    def _deduplicate(self, topics: list[Topic]) -> list[Topic]:
        """Deduplicate by exact hash and fuzzy title matching."""
        seen_hashes: set[str] = set()
        seen_titles: list[str] = []
        unique: list[Topic] = []

        for topic in topics:
            # Exact dedup by id hash
            if topic.id in seen_hashes:
                continue
            seen_hashes.add(topic.id)

            # Fuzzy dedup by normalized title
            norm_title = self._normalize(topic.title)
            is_dup = False
            for existing in seen_titles:
                ratio = SequenceMatcher(None, norm_title, existing).ratio()
                if ratio >= self._sim_threshold:
                    is_dup = True
                    break

            if not is_dup:
                seen_titles.append(norm_title)
                unique.append(topic)

        return unique

    def _normalize(self, title: str) -> str:
        import re
        title = title.lower().strip()
        title = re.sub(r"[^\w\s]", "", title)
        title = re.sub(r"\s+", " ", title)
        return title[:100]

    def _enrich(self, topics: list[Topic]) -> list[Topic]:
        """Calculate viral_score and relevance_score for each topic."""
        max_views = max((t.views for t in topics), default=1) or 1
        max_engagement = max((t.engagement for t in topics), default=1) or 1

        for topic in topics:
            view_factor = min(topic.views / max_views, 1.0)
            engagement_factor = min(topic.engagement / max_engagement, 1.0) if max_engagement > 0 else 0
            recency = self._recency_score(topic)
            source_boost = self._source_boost(topic.source)

            topic.viral_score = round(
                view_factor * 0.3 + engagement_factor * 0.4 + recency * 0.2 + source_boost * 0.1,
                4,
            )
            topic.relevance_score = round(
                view_factor * 0.25 + engagement_factor * 0.25 + recency * 0.25 + source_boost * 0.25,
                4,
            )

        return topics

    def _recency_score(self, topic: Topic) -> float:
        published = topic.published_at
        age_hours = (datetime.now() - published).total_seconds() / 3600
        return max(0.0, 1.0 - age_hours / 48.0)

    def _source_boost(self, source: str) -> float:
        boosts = {
            "youtube": 0.8,
            "trends": 0.9,
            "telegram": 0.7,
            "rss": 0.6,
        }
        return boosts.get(source, 0.5)
