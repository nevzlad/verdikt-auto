"""Trends scanner via pytrends (Google Trends, no API key)."""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from verdikt_auto.scanner.base_scanner import BaseScanner, ScannerConfig
from verdikt_auto.scanner.models import Topic

logger = logging.getLogger(__name__)


class TrendsScanner(BaseScanner):
    """Scan Google Trends for trending topics in RU region."""

    def __init__(self, config: Optional[ScannerConfig] = None) -> None:
        super().__init__(name="trends", config=config, rpm=12)
        self.region = config.get("trends.region", "RU") if config else "RU"
        self.language = config.get("trends.language", "ru") if config else "ru"
        self._trending_keywords: list[str] = []
        self._last_trends_fetch: Optional[datetime] = None

    def configure(self, trending_keywords: Optional[list[str]] = None) -> None:
        self._trending_keywords = trending_keywords or []

    async def health_check(self) -> bool:
        return True

    async def _fetch_trending_searches(self) -> list[str]:
        """Fetch trending searches from Google Trends."""
        await self._rate_limit()
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl=f"{self.language}-{self.region}", tz=180)
            trending = pytrends.trending_searches(pn=self.region)
            if trending is not None and not trending.empty:
                return trending[0].tolist()[:15]
            return []
        except ImportError:
            logger.warning("pytrends not installed (pip install pytrends)")
            return []
        except Exception as exc:
            logger.error("pytrends trending_searches error: %s", exc)
            return []

    async def _fetch_interest_over_time(self, keywords: list[str]) -> list[dict]:
        if not keywords:
            return []
        await self._rate_limit()
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl=f"{self.language}-{self.region}", tz=180)
            pytrends.build_payload(
                kw_list=keywords[:5],
                timeframe="now 7-d",
                geo=self.region,
            )
            interest = pytrends.interest_over_time()
            if interest is not None and not interest.empty:
                last_row = interest.iloc[-1]
                results = []
                for kw in keywords[:5]:
                    if kw in last_row:
                        results.append({"keyword": kw, "score": int(last_row[kw])})
                return results
            return []
        except ImportError:
            return []
        except Exception as exc:
            logger.error("pytrends interest_over_time error: %s", exc)
            return []

    async def scan(self) -> list[Topic]:
        topics: list[Topic] = []

        # Always fetch trending searches
        try:
            trending_items = await self._fetch_trending_searches()
            for item in trending_items:
                topic = Topic.from_raw(
                    title=item,
                    source="trends",
                    source_name="Google Trends",
                    url=f"https://trends.google.com/trends/explore?q={item}&geo={self.region}",
                    published_at=datetime.now(),
                    views=1000,
                    likes=0,
                    keywords=[item.lower()],
                    raw_data={"type": "trending_search", "region": self.region},
                )
                topics.append(topic)

            logger.info("Trends fetched %d trending searches", len(trending_items))
        except Exception as exc:
            self._total_errors += 1
            logger.error("Trends trending_searches error: %s", exc)

        # Fetch interest for configured keywords
        if self._trending_keywords:
            try:
                interest_data = await self._fetch_interest_over_time(self._trending_keywords)
                for data in interest_data:
                    score = data.get("score", 0)
                    if score > 0:
                        kw = data.get("keyword", "")
                        topic = Topic.from_raw(
                            title=f"Trending: {kw}",
                            source="trends",
                            source_name="Google Trends",
                            url=f"https://trends.google.com/trends/explore?q={kw}&geo={self.region}",
                            published_at=datetime.now(),
                            views=score * 100,
                            keywords=[kw],
                            raw_data={"type": "interest_over_time", "score": score},
                        )
                        topic.viral_score = score / 100.0
                        topics.append(topic)

                logger.info("Trends fetched interest for %d keywords", len(interest_data))
            except Exception as exc:
                self._total_errors += 1
                logger.error("Trends interest_over_time error: %s", exc)

        self._total_scanned += len(topics)
        logger.info("Trends scan complete: %d topics", len(topics))
        return topics
