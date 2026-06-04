"""Tests for topic ranker — TF-IDF relevance, viral heuristics, SEO score, debug mode."""

from datetime import datetime

from verdikt_auto.ranker.topic_ranker import TopicRanker
from verdikt_auto.scanner.models import Topic, TopicCategory


class TestNormalize:
    def test_log_normalize(self) -> None:
        assert 0.5 < TopicRanker.normalize(100, 1000, "log") < 1.0
        assert TopicRanker.normalize(0, 1000, "log") == 0.0
        assert TopicRanker.normalize(50, 0, "log") == 0.0

    def test_minmax_normalize(self) -> None:
        assert TopicRanker.normalize(50, 100, "minmax") == 0.5
        assert TopicRanker.normalize(0, 100, "minmax") == 0.0
        assert TopicRanker.normalize(100, 100, "minmax") == 1.0

    def test_clamps_to_one(self) -> None:
        assert TopicRanker.normalize(200, 100, "minmax") == 1.0


class TestTopicRanker:
    def _make_topic(
        self,
        title: str = "Test",
        source: str = "rss",
        views: int = 100,
        engagement: float = 5.0,
        keywords: list[str] | None = None,
        category: TopicCategory = TopicCategory.GENERAL,
        published_at: datetime | None = None,
    ) -> Topic:
        return Topic(
            id=f"id_{title.lower().replace(' ', '_')[:20]}",
            title=title,
            source=source,
            source_name=source,
            url="https://x.com",
            published_at=published_at or datetime.now(),
            views=views,
            engagement=engagement,
            keywords=keywords or [],
            category=category,
        )

    def test_rank_returns_sorted(self) -> None:
        t1 = self._make_topic("Big News Today", views=5000, engagement=25.0, keywords=["news", "trending"])
        t2 = self._make_topic("Small Update", views=50, engagement=1.0)
        ranker = TopicRanker()
        ranked = ranker.rank([t1, t2], posts_per_day=7)
        assert len(ranked) == 2
        assert ranked[0].score >= ranked[1].score

    def test_empty_topics(self) -> None:
        ranker = TopicRanker()
        ranked = ranker.rank([])
        assert ranked == []

    def test_top_n_plus_reserve(self) -> None:
        topics = [self._make_topic(f"Topic {i}", views=(i+1)*100) for i in range(20)]
        ranker = TopicRanker()
        ranked = ranker.rank(topics, posts_per_day=5)
        assert len(ranked) == 10  # 5 + 5 reserve

    def test_views_normalized_log(self) -> None:
        high = self._make_topic("High Views", views=100000, engagement=50.0)
        low = self._make_topic("Low Views", views=10, engagement=0.1)
        ranker = TopicRanker()
        ranked = ranker.rank([low, high], posts_per_day=1)
        assert ranked[0].topic_id == high.id
        assert ranked[0].normalized_views > ranked[1].normalized_views

    def test_relevance_score_tfidf(self) -> None:
        related = self._make_topic("СВО Украина новый конфликт", keywords=["сво", "украина"])
        unrelated = self._make_topic("Weather forecast today", keywords=["weather"])
        ranker = TopicRanker()
        assert ranker._relevance_score(related) > ranker._relevance_score(unrelated)

    def test_relevance_fallback_no_sklearn(self) -> None:
        topic = self._make_topic("Simple test", keywords=["test"])
        ranker = TopicRanker()
        score = ranker._relevance_score(topic)  # no crash, may be 0
        assert isinstance(score, float)

    def test_continuity_bonus_default(self) -> None:
        topic = self._make_topic("Random topic")
        ranker = TopicRanker()
        assert ranker._continuity_bonus(topic) == 0.5

    def test_novelty_score_fresh(self) -> None:
        fresh = self._make_topic(published_at=datetime.now())
        ranker = TopicRanker()
        assert ranker._novelty_score(fresh) > 0.5

    def test_novelty_score_old(self) -> None:
        old = self._make_topic(published_at=datetime(2025, 1, 1))
        ranker = TopicRanker()
        assert ranker._novelty_score(old) < 0.1

    def test_viral_potential_question(self) -> None:
        q = self._make_topic("Что будет с рынком нефти?")
        no_q = self._make_topic("Рынок нефти стабилен")
        ranker = TopicRanker()
        assert ranker._viral_potential(q) > ranker._viral_potential(no_q)

    def test_viral_potential_numbers(self) -> None:
        nums = self._make_topic("5 причин купить биткоин")
        ranker = TopicRanker()
        assert ranker._viral_potential(nums) >= 0.1

    def test_viral_potential_emotional(self) -> None:
        emo = self._make_topic("Шок! Прорыв в термояде")
        ranker = TopicRanker()
        assert ranker._viral_potential(emo) >= 0.15

    def test_seo_score_semantic_core(self) -> None:
        good = self._make_topic("Аналитика и разбор ситуации", keywords=["аналитика", "вердикт"])
        bad = self._make_topic("Погода на завтра")
        ranker = TopicRanker()
        assert ranker._seo_score(good) > ranker._seo_score(bad)

    def test_seo_score_max_one(self) -> None:
        full = self._make_topic(
            "Аналитика разбор вердикт OSINT фейк",
            keywords=["аналитика", "разбор", "вердикт", "osint", "фейк"],
        )
        ranker = TopicRanker()
        assert ranker._seo_score(full) <= 1.0

    def test_custom_weights(self) -> None:
        t1 = self._make_topic("Topic A", views=5000)
        t2 = self._make_topic("Topic B", views=10)
        ranker = TopicRanker(weights={"w1": 1.0, "w2": 0, "w3": 0, "w4": 0, "w5": 0, "w6": 0, "w7": 0})
        ranked = ranker.rank([t1, t2])
        assert ranked[0].topic_id == t1.id

    def test_debug_mode_explanation(self) -> None:
        topic = self._make_topic("Test Debug", views=500, engagement=10.0, keywords=["a", "b"])
        ranker = TopicRanker()
        ranked = ranker.rank([topic], debug=True)
        assert ranked[0].explanation != ""
        assert "score=" in ranked[0].explanation
        assert "views(log)=" in ranked[0].explanation

    def test_all_fields_present(self) -> None:
        topic = self._make_topic("Full Fields", views=500, engagement=10.0, keywords=["a", "b"])
        ranker = TopicRanker()
        ranked = ranker.rank([topic])
        r = ranked[0]
        assert all(
            getattr(r, field) >= 0
            for field in [
                "normalized_views", "normalized_engagement",
                "relevance_score", "continuity_bonus",
                "novelty_score", "viral_potential", "seo_score",
            ]
        )
        assert r.score > 0
