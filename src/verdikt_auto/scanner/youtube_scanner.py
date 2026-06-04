"""YouTube scanner via google-api-python-client."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from verdikt_auto.scanner.base_scanner import BaseScanner, ScannerConfig
from verdikt_auto.scanner.models import Topic, TopicCategory

logger = logging.getLogger(__name__)


class YouTubeScanner(BaseScanner):
    """Scan YouTube channels for latest videos with engagement metrics."""

    def __init__(self, api_key: str, config: Optional[ScannerConfig] = None) -> None:
        super().__init__(name="youtube", config=config, rpm=50)
        self.api_key = api_key
        self.max_results = config.get("youtube.max_results", 10) if config else 10
        self.order = config.get("youtube.order", "date") if config else "date"
        self._channels: list[dict] = []
        self._daily_quota_used = 0
        self._daily_quota_limit = 10000

    def configure(self, channels: list[dict]) -> None:
        self._channels = channels

    async def health_check(self) -> bool:
        return bool(self.api_key)

    def _quota_available(self) -> bool:
        return self._daily_quota_used < self._daily_quota_limit

    async def _fetch_search(self, channel_id: str) -> list[dict]:
        """Call search.list (100 quota units)."""
        await self._rate_limit()
        self._daily_quota_used += 100

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError

            yt = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
            request = yt.search().list(
                part="snippet",
                channelId=channel_id,
                order=self.order,
                maxResults=self.max_results,
                type="video",
            )
            response = request.execute()
            return response.get("items", [])
        except Exception as exc:
            logger.error("YouTube search.list error for channel %s: %s", channel_id, exc)
            return []

    async def _fetch_video_stats(self, video_ids: list[str]) -> dict[str, dict]:
        """Call videos.list (1-2 quota units)."""
        if not video_ids:
            return {}
        await self._rate_limit()
        self._daily_quota_used += 2

        video_data: dict[str, dict] = {}
        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError

            yt = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
            response = yt.videos().list(
                part="statistics,contentDetails",
                id=",".join(video_ids),
            ).execute()

            for item in response.get("items", []):
                vid = item["id"]
                stats = item.get("statistics", {})
                video_data[vid] = {
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                }
        except Exception as exc:
            logger.error("YouTube videos.list error: %s", exc)

        return video_data

    async def scan(self) -> list[Topic]:
        if not self._quota_available():
            logger.warning("YouTube daily quota exhausted (%d/%d)", self._daily_quota_used, self._daily_quota_limit)
            return []

        topics: list[Topic] = []
        for channel in self._channels:
            try:
                channel_id = channel.get("id", "")
                channel_name = channel.get("name", channel_id)
                channel_keywords = channel.get("keywords", [])

                search_items = await self._fetch_search(channel_id)
                video_ids = [
                    item["id"]["videoId"]
                    for item in search_items
                    if item.get("id", {}).get("videoId")
                ]

                stats_map = await self._fetch_video_stats(video_ids)

                for item in search_items:
                    video_id = item.get("id", {}).get("videoId")
                    if not video_id:
                        continue

                    snippet = item.get("snippet", {})
                    title = snippet.get("title", "Untitled")
                    description = snippet.get("description", "")
                    published_raw = snippet.get("publishedAt")
                    published = None
                    if published_raw:
                        try:
                            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            published = datetime.now()

                    stats = stats_map.get(video_id, {})
                    keywords = channel_keywords + self._extract_keywords(title, description)

                    topic = Topic.from_raw(
                        title=title,
                        source="youtube",
                        source_name=channel_name,
                        url=f"https://youtube.com/watch?v={video_id}",
                        published_at=published,
                        views=stats.get("views", 0),
                        likes=stats.get("likes", 0),
                        comments=stats.get("comments", 0),
                        keywords=keywords,
                        raw_data={
                            "channel_id": channel_id,
                            "video_id": video_id,
                            "description": description[:500],
                        },
                    )
                    topics.append(topic)

                self._total_scanned += len(search_items)
                logger.info("YouTube scanned %d videos from %s", len(search_items), channel_name)

            except Exception as exc:
                self._total_errors += 1
                logger.error("YouTube scan error for channel %s: %s", channel.get("id"), exc)

        logger.info("YouTube scan complete: %d topics, quota=%d/%d", len(topics), self._daily_quota_used, self._daily_quota_limit)
        return topics

    def _extract_keywords(self, title: str, description: str) -> list[str]:
        text = f"{title} {description}".lower()
        words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{4,}", text)
        stop_words = {
            "это", "что", "как", "для", "все", "так", "еще", "когда", "уже",
            "the", "and", "for", "are", "not", "you", "all", "can", "was",
        }
        return [w for w in words if w not in stop_words][:5]
