"""Tests for analytics modules."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.analytics.chat_analyzer import (
    ChatReader,
    PainPointDetector,
    PriorityUpdater,
    TopicExtractor,
)
from verdikt_auto.analytics.metrics import MetricsCollector, PostPerformanceTracker
from verdikt_auto.analytics.strategy import (
    PredictiveTopicFinder,
    StrategyAdjuster,
    WeeklyReportGenerator,
)
from verdikt_auto.core.models import GeneratedContent, ProviderName, RankedTopic, TaskType


class TestPostPerformanceTracker:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tracker = PostPerformanceTracker(db_path=self.tmp.name)

    def teardown_method(self) -> None:
        try:
            self.tracker.close()
        except Exception:
            pass
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except PermissionError:
            pass

    def test_record_and_get(self) -> None:
        self.tracker.record("post_1", "1h", views=100, reactions=5)
        data = self.tracker.get("post_1")
        assert "1h" in data
        assert data["1h"]["views"] == 100

    def test_record_updates(self) -> None:
        self.tracker.record("post_1", "1h", views=100)
        self.tracker.record("post_1", "1h", views=150)
        data = self.tracker.get("post_1")
        assert data["1h"]["views"] == 150

    def test_get_all_recent(self) -> None:
        self.tracker.record("post_1", "1h", views=50)
        recent = self.tracker.get_all_recent(hours=24)
        assert len(recent) >= 1

    def test_daily_stats(self) -> None:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        self.tracker.record_daily(today, posts=3, views=500, new_subscribers=10)
        daily = self.tracker.get_daily(days=7)
        assert any(d["date"] == today for d in daily)
        d = [x for x in daily if x["date"] == today][0]
        assert d["posts"] == 3
        assert d["views"] == 500


class TestMetricsCollector:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.collector = MetricsCollector(db_path=self.tmp.name)

    def teardown_method(self) -> None:
        try:
            self.collector.close()
        except Exception:
            pass
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except PermissionError:
            pass

    def test_collect_daily_no_bot(self) -> None:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        self.collector.tracker.record_daily(today, new_subscribers=50)
        daily = self.collector.tracker.get_daily(1)
        assert any(d["date"] == today for d in daily)

    def test_channel_stats_empty(self) -> None:
        stats = self.collector.get_channel_stats()
        assert stats.posts_count == 0
        assert stats.views_last_7d == 0


class TestTopicExtractor:
    def setup_method(self) -> None:
        self.extractor = TopicExtractor()

    def test_extract_keywords_empty(self) -> None:
        assert self.extractor.extract_keywords([]) == []

    def test_extract_keywords_freq(self) -> None:
        texts = ["экономика кризис инфляция", "инфляция растет экономика", "кризис экономика"]
        kws = self.extractor.extract_keywords(texts, top_n=3)
        words = [kw for kw, _ in kws]
        assert "экономика" in words
        assert len(kws) <= 3

    def test_cluster_topics_fallback(self) -> None:
        texts = ["один", "два"]
        clusters = self.extractor.cluster_topics(texts, n_clusters=3)
        assert isinstance(clusters, list)
        assert len(clusters) > 0


class TestPainPointDetector:
    @pytest.mark.asyncio
    async def test_detect_empty(self) -> None:
        router = MagicMock()
        detector = PainPointDetector(router)
        result = await detector.detect([])
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_parsed(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.analyze_chat,
            provider=ProviderName.groq,
            text="- Боль: не хватает аналитики\n  Частота: высокая\n  - Предложение: добавить разбор",
        ))
        detector = PainPointDetector(router)
        result = await detector.detect(["комментарий раз"])
        assert len(result) >= 1
        assert any("боль" in p.get("raw", "").lower() for p in result)


class TestPriorityUpdater:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write("[]")
        self.tmp.close()
        self.updater = PriorityUpdater(path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_add_and_get(self) -> None:
        self.updater.add("экономика")
        assert "экономика" in self.updater.get_all()

    def test_add_duplicate(self) -> None:
        self.updater.add("тема")
        self.updater.add("тема")
        assert len(self.updater.get_all()) == 1

    def test_add_batch(self) -> None:
        self.updater.add_batch(["a", "b", "a"])
        assert len(self.updater.get_all()) == 2

    def test_remove(self) -> None:
        self.updater.add("тест")
        self.updater.remove("тест")
        assert "тест" not in self.updater.get_all()


class TestStrategyAdjuster:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"w1": 0.20, "w2": 0.20}')
        self.tmp.close()
        self.adjuster = StrategyAdjuster(weights_path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_get_weights(self) -> None:
        w = self.adjuster.get_weights()
        assert "w1" in w

    def test_adjust_increase(self) -> None:
        changes = self.adjuster.adjust({"w1": {"avg_engagement": 0.8}})
        assert self.adjuster.get_weights()["w1"] > 0.20

    def test_adjust_decrease(self) -> None:
        changes = self.adjuster.adjust({"w1": {"avg_engagement": 0.2}})
        assert self.adjuster.get_weights()["w1"] < 0.20

    def test_normalize(self) -> None:
        self.adjuster.adjust({"w1": {"avg_engagement": 0.9}})
        total = sum(self.adjuster.get_weights().values())
        assert abs(total - 1.0) < 0.01


class TestWeeklyReportGenerator:
    @pytest.mark.asyncio
    async def test_template_report(self) -> None:
        gen = WeeklyReportGenerator()
        stats = {"posts_count": 10, "views_last_7d": 500, "avg_engagement": 3.5}
        topics = [RankedTopic(topic_id="1", title="Test", score=0.9)]
        report = await gen.generate(stats, topics)
        assert "VERDIKT" in report
        assert "10" in report

    @pytest.mark.asyncio
    async def test_ai_report(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="📊 **AI Report**\n\nОтличная неделя!",
        ))
        gen = WeeklyReportGenerator(router=router)
        stats = {"posts_count": 5}
        topics = [RankedTopic(topic_id="1", title="AI", score=0.95)]
        report = await gen.generate(stats, topics)
        assert "AI Report" in report or "Отличная" in report


class TestPredictiveTopicFinder:
    @pytest.mark.asyncio
    async def test_predict(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="1. Название: Кризис экономики\n   Прогноз просмотров: 10000\n   Почему: актуально\n   Формат: статья",
        ))
        finder = PredictiveTopicFinder(router)
        topics = [RankedTopic(topic_id="1", title="Экономика", score=0.8)]
        preds = await finder.predict(topics, [])
        assert len(preds) > 0

    @pytest.mark.asyncio
    async def test_predict_empty(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="",
        ))
        finder = PredictiveTopicFinder(router)
        preds = await finder.predict([], [])
        assert preds == []
