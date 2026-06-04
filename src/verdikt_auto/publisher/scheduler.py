"""Pipeline scheduler — orchestrates scan → rank → generate → publish → analytics."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from verdikt_auto.analytics.metrics import MetricsCollector, PostPerformanceTracker
from verdikt_auto.analytics.strategy import StrategyAdjuster
from verdikt_auto.anti_ban.shield import AntiBanShield
from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.config import Settings
from verdikt_auto.core.models import Post, PostCategory, RankedTopic
from verdikt_auto.dashboard.server import DashboardServer
from verdikt_auto.formatter.image_generator import ImageGenerator
from verdikt_auto.formatter.text_formatter import TextFormatter
from verdikt_auto.formatter.tts_generator import TTSGenerator
from verdikt_auto.generator.content_generator import ContentGenerator
from verdikt_auto.parser.post_parser import PostParser
from verdikt_auto.parser.validator import ContentValidator
from verdikt_auto.publisher.telegram_publisher import PostScheduler, TelegramPublisher
from verdikt_auto.ranker.topic_ranker import TopicRanker
from verdikt_auto.scanner.models import Topic
from verdikt_auto.scanner.sources_manager import SourcesManager
from verdikt_auto.scanner.topic_aggregator import TopicAggregator
from verdikt_auto.seo.headline_optimizer import HeadlineOptimizer
from verdikt_auto.seo.telegram_seo import TelegramSEOOptimizer

logger = logging.getLogger(__name__)

PIPELINE_INTERVAL_SECONDS = 4 * 3600


class Scheduler:
    """Orchestrates the full pipeline: scan → rank → generate → publish → analytics."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.router = AIRouter(settings)
        self.tracker = PostPerformanceTracker(
            db_path=settings.database_path.replace(".db", "_metrics.db"),
        )
        self.metrics = MetricsCollector(
            db_path=settings.database_path.replace(".db", "_metrics.db"),
        )
        self.strategy = StrategyAdjuster()
        self.publisher = TelegramPublisher(settings)
        self.post_scheduler = PostScheduler(
            publisher=self.publisher,
            schedule_hours=settings.schedule_hours,
            posts_per_day=settings.posts_per_day,
        )
        self.anti_ban = AntiBanShield()
        self.generator = ContentGenerator(self.router)
        self.ranker = TopicRanker(weights=settings.topic_weights)
        self.validator = ContentValidator()
        self.parser = PostParser()
        self.text_formatter = TextFormatter()
        self.seo_optimizer = TelegramSEOOptimizer()
        self.headline_optimizer = HeadlineOptimizer(self.router)
        self.image_generator = ImageGenerator(settings)
        self.tts_generator = TTSGenerator(settings)

        scanners = self._init_scanners()
        self.aggregator = TopicAggregator(scanners)

        self.dashboard = DashboardServer(settings, metrics_collector=self.metrics)
        self._running = False

    def _init_scanners(self) -> list:
        sources = SourcesManager()
        sources.load()
        scanners = []
        try:
            from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
            youtube_cfgs = sources.get_sources("youtube")
            if youtube_cfgs:
                scanners.append(YouTubeScanner(youtube_cfgs, api_key=self.settings.scanner.youtube_api_key))
        except Exception as exc:
            logger.warning("YouTube scanner init failed: %s", exc)

        try:
            from verdikt_auto.scanner.telegram_scanner import TelegramScanner
            tg_cfgs = sources.get_sources("telegram")
            if tg_cfgs:
                scanners.append(TelegramScanner(tg_cfgs))
        except Exception as exc:
            logger.warning("Telegram scanner init failed: %s", exc)

        try:
            from verdikt_auto.scanner.rss_scanner import RSSScanner
            rss_cfgs = sources.get_sources("rss")
            if rss_cfgs:
                scanners.append(RSSScanner(rss_cfgs))
        except Exception as exc:
            logger.warning("RSS scanner init failed: %s", exc)

        try:
            from verdikt_auto.scanner.trends_scanner import TrendsScanner
            scanners.append(TrendsScanner())
        except Exception as exc:
            logger.warning("Trends scanner init failed: %s", exc)

        return scanners

    def run_forever(self) -> None:
        self._running = True
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        logger.info("Scheduler started with %d scanners", len(self.aggregator.scanners))

        await self.dashboard.start_async()
        pipeline_task = asyncio.create_task(self._pipeline_loop())
        scheduler_task = asyncio.create_task(self.post_scheduler.run_loop())

        try:
            await asyncio.gather(pipeline_task, scheduler_task)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self.post_scheduler.stop()
            self.tracker.close()
            self.metrics.close()

    async def _pipeline_loop(self) -> None:
        while self._running:
            await self._run_pipeline()
            logger.info("Pipeline complete. Next run in %d seconds", PIPELINE_INTERVAL_SECONDS)
            await asyncio.sleep(PIPELINE_INTERVAL_SECONDS)

    async def _run_pipeline(self) -> None:
        logger.info("=== Pipeline run starting ===")

        topics = await self._scan()
        if not topics:
            logger.warning("No topics collected, skipping pipeline run")
            return

        ranked = self._rank(topics)
        if not ranked:
            logger.warning("No topics survived ranking, skipping pipeline run")
            return

        posts = await self._generate_posts(ranked)
        if not posts:
            logger.warning("No posts generated, skipping pipeline run")
            return

        await self._publish(posts)
        self._update_dashboard(posts)

        logger.info("=== Pipeline run complete: %d posts ===", len(posts))

    async def _scan(self) -> list[Topic]:
        logger.info("Phase 1: Scanning sources")
        topics = await self.aggregator.collect(top_n=30)
        logger.info("Collected %d topics", len(topics))
        return topics

    def _rank(self, topics: list[Topic]) -> list[RankedTopic]:
        logger.info("Phase 2: Ranking topics")
        strategy_weights = self.strategy.get_weights()
        self.ranker.weights.update(strategy_weights)
        ranked = self.ranker.rank(topics, top_n=self.settings.posts_per_day + 5)
        logger.info("Ranked %d topics", len(ranked))
        return ranked

    async def _generate_posts(self, ranked: list[RankedTopic]) -> list[Post]:
        logger.info("Phase 3: Generating posts")
        posts: list[Post] = []
        for topic in ranked[: self.settings.posts_per_day]:
            try:
                post = await self._generate_single_post(topic)
                if post:
                    posts.append(post)
            except Exception as exc:
                logger.error("Post generation failed for topic '%s': %s", topic.title[:40], exc)
        return posts

    async def _generate_single_post(self, topic: RankedTopic) -> Optional[Post]:
        prompt = f"Напиши аналитический пост на тему: {topic.title}\n\n{topic.explanation}"
        gen_result = await self.generator.generate_post(topic, prompt)

        parsed = self.parser.parse(gen_result.text)
        validated = self.validator.validate(parsed)
        if not validated.get("valid", False):
            logger.warning("Post validation failed for '%s': %s", topic.title[:40], validated.get("reason", ""))
            return None

        category = PostCategory.from_keywords([topic.title] + topic.explanation.split())
        headlines = await self.generator.generate_headlines(gen_result.text, category)
        best_headline = self.headline_optimizer.select_headline(headlines, topic.title)

        image_prompt = await self.generator.generate_image_prompt(gen_result.text, category)
        image_path = await self.image_generator.generate_image(image_prompt, category)
        tts_text = await self.generator.generate_tts_text(gen_result.text)
        tts_path = await self.tts_generator.generate_tts(tts_text)

        post = Post(
            topic=topic.title,
            content=type("obj", (object,), {"text": gen_result.text})(),
            headline=best_headline.text if best_headline else "",
            headlines=headlines,
            image_prompt=image_prompt,
            image_url=image_path,
            tts_text=tts_text,
            tts_path=tts_path,
            category=category,
            tags=[topic.title.split()[0].lower()] if topic.title else [],
        )
        return post

    async def _publish(self, posts: list[Post]) -> None:
        logger.info("Phase 4: Enqueuing %d posts for publishing", len(posts))
        for post in posts:
            await self.post_scheduler.enqueue(post)

        allowed = self.anti_ban.check_publish_allowed()
        if not allowed:
            logger.warning("Anti-ban shield blocked publishing")
            await self.publisher.notify_admin("⚠️ Publishing blocked by AntiBanShield")

    def _update_dashboard(self, posts: list[Post]) -> None:
        daily = self.tracker.get_daily(7)
        self.dashboard.update_daily_stats(daily)

        stats = self.metrics.get_channel_stats()
        self.dashboard.update_stats(stats)

        post_list = [
            {"id": p.id or f"post_{i}", "topic": p.topic, "category": p.category.value, "status": "published"}
            for i, p in enumerate(posts)
        ]
        self.dashboard.update_post_stats(post_list)

    def stop(self) -> None:
        self._running = False
        self.post_scheduler.stop()
