"""Tests for scanner module."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.scanner.base_scanner import BaseScanner, ScannerConfig
from verdikt_auto.scanner.models import SourceConfig, Topic, TopicCategory
from verdikt_auto.scanner.rss_scanner import RSSScanner
from verdikt_auto.scanner.trends_scanner import TrendsScanner
from verdikt_auto.scanner.sources_manager import SourcesManager
from verdikt_auto.scanner.topics_db import TopicsDB
from verdikt_auto.scanner.topic_aggregator import TopicAggregator


class TestTopicModel:
    def test_create_id(self) -> None:
        t1 = Topic.create_id("Test Title", "youtube")
        t2 = Topic.create_id("test title   ", "youtube")
        t3 = Topic.create_id("Different", "youtube")
        assert t1 == t2
        assert t1 != t3
        assert len(t1) == 16

    def test_from_raw_minimal(self) -> None:
        topic = Topic.from_raw(
            title="Test Post",
            source="youtube",
            source_name="TestChannel",
            url="https://example.com",
            published_at=None,
        )
        assert topic.title == "Test Post"
        assert topic.source == "youtube"
        assert topic.views == 0
        assert topic.engagement == 0.0
        assert topic.category == TopicCategory.GENERAL

    def test_from_raw_with_engagement(self) -> None:
        topic = Topic.from_raw(
            title="Popular Post",
            source="telegram",
            source_name="@channel",
            url="https://t.me/channel/1",
            published_at=datetime(2026, 1, 1),
            views=1000,
            likes=50,
            comments=10,
            shares=5,
        )
        assert topic.views == 1000
        assert topic.engagement == 6.5
        assert topic.source == "telegram"

    def test_category_classification(self) -> None:
        assert TopicCategory.from_keywords(["сво", "украина"]) == TopicCategory.SVO
        assert TopicCategory.from_keywords(["израиль", "сектор газа"]) == TopicCategory.MIDEAST
        assert TopicCategory.from_keywords(["сша", "трамп"]) == TopicCategory.USA
        assert TopicCategory.from_keywords(["экономика", "нефть"]) == TopicCategory.ECONOMY
        assert TopicCategory.from_keywords(["AI", "нейросеть"]) == TopicCategory.TECH
        assert TopicCategory.from_keywords(["спорт"]) == TopicCategory.GENERAL


class TestSourceConfig:
    def test_to_dict(self) -> None:
        sc = SourceConfig("youtube", "Тест канал", "UC123", keywords=["news"])
        d = sc.to_dict()
        assert d["type"] == "youtube"
        assert d["name"] == "Тест канал"
        assert d["identifier"] == "UC123"
        assert d["keywords"] == ["news"]

    def test_default_keywords(self) -> None:
        sc = SourceConfig("rss", "feed", "url")
        assert sc.keywords == []


class TestScannerConfig:
    def test_get_nested(self) -> None:
        cfg = ScannerConfig({"youtube": {"max_results": 15}})
        assert cfg.get("youtube.max_results") == 15

    def test_get_default(self) -> None:
        cfg = ScannerConfig({})
        assert cfg.get("nonexistent.key", "fallback") == "fallback"


class TestRSSScanner:
    @pytest.mark.asyncio
    async def test_scan_empty_feeds(self) -> None:
        scanner = RSSScanner()
        scanner.configure([])
        topics = await scanner.scan()
        assert topics == []

    @pytest.mark.asyncio
    async def test_health_check_no_feeds(self) -> None:
        scanner = RSSScanner()
        assert await scanner.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_with_feeds(self) -> None:
        scanner = RSSScanner()
        scanner.configure([{"url": "https://example.com/feed"}])
        assert await scanner.health_check() is True

    @pytest.mark.asyncio
    async def test_fetch_and_parse_http_error(self) -> None:
        scanner = RSSScanner()
        scanner.configure([{"url": "https://error.com/feed"}])
        topics = await scanner.scan()
        assert topics == []

    def test_normalize(self) -> None:
        scanner = RSSScanner()
        topic = scanner.normalize({
            "title": "Test Article",
            "source_name": "example.com",
            "url": "https://example.com/article",
            "views": 100,
            "likes": 5,
        })
        assert topic.title == "Test Article"
        assert topic.source == "rss"

    def test_stats(self) -> None:
        scanner = RSSScanner()
        stats = scanner.get_stats()
        assert stats["name"] == "rss"
        assert stats["total_scanned"] == 0
        assert stats["total_errors"] == 0


class TestTrendsScanner:
    @pytest.mark.asyncio
    async def test_scan_empty(self) -> None:
        scanner = TrendsScanner()
        topics = await scanner.scan()
        assert isinstance(topics, list)

    @pytest.mark.asyncio
    async def test_health_check(self) -> None:
        scanner = TrendsScanner()
        assert await scanner.health_check() is True


class TestSourcesManager:
    def test_load_missing_file(self) -> None:
        mgr = SourcesManager("nonexistent.yaml")
        mgr.load()
        counts = mgr.get_all_counts()
        assert all(c == 0 for c in counts.values())

    def test_remove_source(self) -> None:
        mgr = SourcesManager("nonexistent.yaml")
        result = mgr.remove_source("youtube", "UC123")
        assert result is False

    def test_add_and_validate(self) -> None:
        mgr = SourcesManager("nonexistent.yaml")
        sc = SourceConfig("youtube", "Test", "UC123", keywords=["news"])
        mgr.add_source("youtube", sc)
        assert mgr.get_all_counts()["youtube"] == 1
        result = mgr.validate_all()
        assert result["youtube:UC123"] is True

    def test_add_unknown_type(self) -> None:
        mgr = SourcesManager("nonexistent.yaml")
        sc = SourceConfig("unknown", "x", "x")
        with pytest.raises(ValueError):
            mgr.add_source("unknown", sc)

    def test_get_youtube_channels_format(self) -> None:
        mgr = SourcesManager("nonexistent.yaml")
        sc = SourceConfig("youtube", "MyChannel", "UC123", keywords=["news"])
        mgr.add_source("youtube", sc)
        channels = mgr.get_youtube_channels()
        assert channels[0]["id"] == "UC123"
        assert channels[0]["name"] == "MyChannel"
        assert channels[0]["keywords"] == ["news"]

    def test_get_telegram_channels(self) -> None:
        mgr = SourcesManager("nonexistent.yaml")
        sc = SourceConfig("telegram", "@channel", "@channel")
        mgr.add_source("telegram", sc)
        assert mgr.get_telegram_channels() == ["@channel"]


class TestTopicsDB:
    @pytest.fixture
    def db(self, tmp_path) -> TopicsDB:
        path = tmp_path / "test_topics.db"
        return TopicsDB(str(path))

    def test_add_and_count(self, db: TopicsDB) -> None:
        topic = Topic.from_raw(
            title="Test", source="youtube", source_name="Chan",
            url="https://x.com", published_at=datetime.now(),
        )
        assert db.add_topic(topic) is True
        assert db.count() == 1

    def test_deduplicate(self, db: TopicsDB) -> None:
        now = datetime.now()
        t1 = Topic.from_raw(
            title="Dup Title", source="youtube", source_name="Chan",
            url="https://x.com/1", published_at=now, views=100,
        )
        t2 = Topic.from_raw(
            title="Dup Title", source="telegram", source_name="Chan",
            url="https://x.com/2", published_at=now, views=50,
        )
        # Different sources -> different IDs, both inserted
        assert db.add_topic(t1) is True
        assert db.add_topic(t2) is True
        assert db.count() == 2
        deleted = db.deduplicate()
        assert db.count() == 1

    def test_get_recent(self, db: TopicsDB) -> None:
        now = datetime.now()
        t1 = Topic.from_raw(title="A", source="rss", source_name="X", url="https://x.com/a", published_at=now)
        t2 = Topic.from_raw(title="B", source="rss", source_name="X", url="https://x.com/b", published_at=now)
        db.add_topics([t1, t2])
        recent = db.get_recent(limit=10)
        assert len(recent) == 2

    def test_search(self, db: TopicsDB) -> None:
        t = Topic.from_raw(title="Breaking News", source="rss", source_name="X", url="https://x.com", published_at=datetime.now())
        db.add_topic(t)
        results = db.search("Breaking")
        assert len(results) == 1
        results = db.search("Nonexistent")
        assert len(results) == 0

    def test_add_topic_duplicate(self, db: TopicsDB) -> None:
        t = Topic.from_raw(title="Unique", source="rss", source_name="X", url="https://x.com", published_at=datetime.now())
        assert db.add_topic(t) is True
        assert db.add_topic(t) is False

    def test_close(self, db: TopicsDB) -> None:
        db.close()
        db.close()


class TestTopicAggregator:
    class FakeScanner(BaseScanner):
        def __init__(self, name: str, topics: list[Topic]) -> None:
            super().__init__(name=name)
            self._topics = topics

        async def scan(self) -> list[Topic]:
            return self._topics

    class FailingScanner(BaseScanner):
        def __init__(self) -> None:
            super().__init__(name="failing")

        async def scan(self) -> list[Topic]:
            raise RuntimeError("scan failed")

    @pytest.mark.asyncio
    async def test_collect_empty(self) -> None:
        agg = TopicAggregator(scanners=[])
        topics = await agg.collect(top_n=10)
        assert topics == []

    @pytest.mark.asyncio
    async def test_collect_with_topics(self) -> None:
        now = datetime.now()
        t1 = Topic.from_raw(title="Global Economy Update", source="rss", source_name="X", url="https://x.com/1", published_at=now, views=500, likes=20)
        t2 = Topic.from_raw(title="Tech AI Breakthrough", source="rss", source_name="X", url="https://x.com/2", published_at=now, views=100, likes=5)
        scanner = self.FakeScanner("test", [t1, t2])
        agg = TopicAggregator(scanners=[scanner])
        results = await agg.collect(top_n=5)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_failing_scanner(self) -> None:
        t = Topic.from_raw(title="Ok", source="rss", source_name="X", url="https://x.com", published_at=datetime.now())
        ok = self.FakeScanner("ok", [t])
        fail = self.FailingScanner()
        agg = TopicAggregator(scanners=[ok, fail])
        results = await agg.collect()
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_dedup_same_title(self) -> None:
        now = datetime.now()
        t1 = Topic.from_raw(title="Same Title Here", source="rss", source_name="X", url="https://x.com/1", published_at=now, views=10)
        t2 = Topic.from_raw(title="Same Title Here", source="rss", source_name="X", url="https://x.com/2", published_at=now, views=10)
        scanner = self.FakeScanner("test", [t1, t2])
        agg = TopicAggregator(scanners=[scanner])
        results = await agg.collect()
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_enrich_scores(self) -> None:
        now = datetime.now()
        t1 = Topic.from_raw(title="High", source="youtube", source_name="Chan", url="https://x.com/1", published_at=now, views=1000, likes=100, comments=10, shares=5)
        t2 = Topic.from_raw(title="Low", source="rss", source_name="X", url="https://x.com/2", published_at=now, views=10, likes=0)
        scanner = self.FakeScanner("test", [t1, t2])
        agg = TopicAggregator(scanners=[scanner])
        results = await agg.collect(top_n=2)
        assert results[0].viral_score >= results[1].viral_score
        assert all(t.relevance_score > 0 for t in results)

    @pytest.mark.asyncio
    async def test_top_n_filtering(self) -> None:
        now = datetime.now()
        titles = [
            "Global Economy Crisis", "Tech AI Revolution", "Sports Champions Win",
            "Weather Climate Change", "Health New Vaccine", "Space Mars Mission",
            "Finance Stock Market", "Education Online Learning", "Politics Election Results",
            "Culture Art Exhibition",
        ]
        topics = []
        for i, t in enumerate(titles):
            topic = Topic.from_raw(title=t, source="rss", source_name="X", url=f"https://x.com/{i}", published_at=now, views=(i+1)*100)
            topics.append(topic)
        scanner = self.FakeScanner("test", topics)
        agg = TopicAggregator(scanners=[scanner])
        results = await agg.collect(top_n=3)
        assert len(results) == 3


class TestBaseScanner:
    @pytest.mark.asyncio
    async def test_rate_limit_no_block(self) -> None:
        scanner = TestTopicAggregator.FakeScanner("test", [])
        start = datetime.now()
        await scanner._rate_limit()
        elapsed = (datetime.now() - start).total_seconds()
        assert elapsed < 1.0

    def test_get_stats(self) -> None:
        scanner = TestTopicAggregator.FakeScanner("test", [])
        stats = scanner.get_stats()
        assert stats["name"] == "test"

    @pytest.mark.asyncio
    async def test_health_check_default(self) -> None:
        scanner = TestTopicAggregator.FakeScanner("test", [])
        assert await scanner.health_check() is True

    def test_normalize_basic(self) -> None:
        scanner = TestTopicAggregator.FakeScanner("test", [])
        topic = scanner.normalize({"title": "Normalized"})
        assert topic.title == "Normalized"
        assert topic.source == "test"


class TestYouTubeScanner:
    @pytest.mark.asyncio
    async def test_health_check_with_key(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="fake_key")
        assert await scanner.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_no_key(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="")
        assert await scanner.health_check() is False

    def test_quota_available(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="key")
        assert scanner._quota_available() is True

    def test_quota_exhausted(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="key")
        scanner._daily_quota_used = 10000
        assert scanner._quota_available() is False

    @pytest.mark.asyncio
    async def test_scan_empty_when_quota_exhausted(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="key")
        scanner._daily_quota_used = 10000
        topics = await scanner.scan()
        assert topics == []

    @pytest.mark.asyncio
    async def test_scan_with_channels(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner

        mock_search_items = [
            {
                "id": {"videoId": "vid1"},
                "snippet": {
                    "title": "Тест видео экономика",
                    "description": "Обзор экономической ситуации",
                    "publishedAt": "2026-01-15T10:00:00Z",
                    "channelTitle": "NewsChannel",
                },
            },
        ]
        mock_stats = {
            "vid1": {"views": 5000, "likes": 200, "comments": 30},
        }

        scanner = YouTubeScanner(api_key="fake_key")
        scanner.configure([
            {"id": "UC123", "name": "NewsChannel", "keywords": ["экономика"]},
        ])

        scanner._fetch_search = AsyncMock(return_value=mock_search_items)
        scanner._fetch_video_stats = AsyncMock(return_value=mock_stats)

        topics = await scanner.scan()
        assert len(topics) == 1
        assert topics[0].title == "Тест видео экономика"
        assert topics[0].source == "youtube"
        assert topics[0].views == 5000
        assert "экономика" in topics[0].keywords

    @pytest.mark.asyncio
    async def test_scan_skips_missing_video_id(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner

        scanner = YouTubeScanner(api_key="key")
        scanner.configure([{"id": "UC1", "name": "C1", "keywords": []}])
        scanner._fetch_search = AsyncMock(return_value=[
            {"id": {}, "snippet": {"title": "No ID", "description": "", "publishedAt": None}},
        ])
        scanner._fetch_video_stats = AsyncMock(return_value={})

        topics = await scanner.scan()
        assert len(topics) == 0

    @pytest.mark.asyncio
    async def test_scan_handles_channel_error(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner

        scanner = YouTubeScanner(api_key="key")
        scanner.configure([{"id": "UC_BAD", "name": "Bad", "keywords": []}])
        scanner._fetch_search = AsyncMock(side_effect=Exception("API error"))

        topics = await scanner.scan()
        assert topics == []
        assert scanner._total_errors == 1

    @pytest.mark.asyncio
    async def test_fetch_video_stats_empty_ids(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="key")
        stats = await scanner._fetch_video_stats([])
        assert stats == {}

    def test_extract_keywords(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="key")
        kws = scanner._extract_keywords("Экономика России 2026", "Обзор и анализ")
        assert "экономика" in kws
        assert "россии" in kws
        assert len(kws) <= 5

    def test_extract_keywords_stop_words_filtered(self) -> None:
        from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
        scanner = YouTubeScanner(api_key="key")
        kws = scanner._extract_keywords("это что для", "the and for")
        assert kws == []


class TestTelegramScanner:
    @pytest.mark.asyncio
    async def test_health_check_with_channels(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        scanner.configure(["@channel"])
        assert await scanner.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_no_channels(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        assert await scanner.health_check() is False

    @pytest.mark.asyncio
    async def test_get_client_import_error(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        with patch.dict("sys.modules", {"telethon": None}):
            client = scanner._get_client()
            assert client is None

    @pytest.mark.asyncio
    async def test_scan_no_client(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        scanner.configure(["@channel"])
        scanner._get_client = MagicMock(return_value=None)
        topics = await scanner.scan()
        assert topics == []

    @pytest.mark.asyncio
    async def test_scan_with_mocked_telethon(self) -> None:
        from datetime import timedelta
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner

        mock_msg = MagicMock()
        mock_msg.text = "Экономика России: новый прогноз на 2026 год\n\nПодробный анализ ситуации"
        mock_msg.id = 101
        mock_msg.date = datetime.now()
        mock_msg.views = 1500
        mock_msg.forwards = 50

        mock_reaction = MagicMock()
        mock_reaction.count = 25
        mock_msg.reactions.results = [mock_reaction]

        mock_client = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=False)
        mock_client.connect = AsyncMock()
        mock_client.get_entity = AsyncMock(return_value="entity")
        mock_client.get_messages = AsyncMock(return_value=[mock_msg])

        scanner = TelegramScanner()
        scanner.configure(["@news_channel"])
        scanner._get_client = MagicMock(return_value=mock_client)

        topics = await scanner.scan()
        assert len(topics) == 1
        assert topics[0].source == "telegram"
        assert topics[0].views == 1500
        assert "экономика" in topics[0].keywords
        assert "россии" in topics[0].keywords
        assert topics[0].url == "https://t.me/news_channel/101"

    @pytest.mark.asyncio
    async def test_scan_skips_empty_messages(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner

        mock_msg_empty = MagicMock()
        mock_msg_empty.text = None
        mock_msg_empty.id = 1
        mock_msg_empty.date = datetime.now()
        mock_msg_empty.views = 0
        mock_msg_empty.forwards = 0
        mock_msg_empty.reactions = None

        mock_client = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=False)
        mock_client.connect = AsyncMock()
        mock_client.get_entity = AsyncMock(return_value="entity")
        mock_client.get_messages = AsyncMock(return_value=[mock_msg_empty])

        scanner = TelegramScanner()
        scanner.configure(["@channel"])
        scanner._get_client = MagicMock(return_value=mock_client)

        topics = await scanner.scan()
        assert topics == []

    @pytest.mark.asyncio
    async def test_scan_handles_channel_error(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner

        mock_client = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=False)
        mock_client.connect = AsyncMock()
        mock_client.get_entity = AsyncMock(side_effect=Exception("Flood wait"))

        scanner = TelegramScanner()
        scanner.configure(["@bad_channel"])
        scanner._get_client = MagicMock(return_value=mock_client)

        topics = await scanner.scan()
        assert topics == []
        assert scanner._total_errors == 1

    def test_extract_keywords(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        kws = scanner._extract_keywords("Экономика России инфляция растет")
        assert "экономика" in kws
        assert "россии" in kws
        assert len(kws) <= 10

    def test_extract_keywords_stop_words(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        kws = scanner._extract_keywords("это что как для все")
        assert kws == []

    @pytest.mark.asyncio
    async def test_close_disconnects(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner

        mock_client = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=True)
        mock_client.disconnect = AsyncMock()

        scanner = TelegramScanner()
        scanner._client = mock_client
        await scanner.close()
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_no_client(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        await scanner.close()

    def test_configure(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        scanner.configure(["@a", "@b"])
        assert scanner._channels == ["@a", "@b"]

    def test_get_stats(self) -> None:
        from verdikt_auto.scanner.telegram_scanner import TelegramScanner
        scanner = TelegramScanner()
        stats = scanner.get_stats()
        assert stats["name"] == "telegram"
