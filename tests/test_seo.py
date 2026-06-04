"""Tests for SEO module: TelegramSEOOptimizer, HeadlineOptimizer."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.core.models import GeneratedContent, HeadlineType, HeadlineVariant, Post, PostCategory, ProviderName, TaskType
from verdikt_auto.seo.headline_optimizer import HeadlineHistory, HeadlineOptimizer, HeadlineScorer, HeadlineSelector
from verdikt_auto.seo.telegram_seo import KeywordDB, TelegramSEOOptimizer


class TestKeywordDB:
    def setup_method(self) -> None:
        self.db = KeywordDB(":memory:")

    def test_record_keyword(self) -> None:
        self.db.record_keyword("тест", "post_1")
        top = self.db.get_top_keywords(10)
        assert len(top) >= 1
        assert top[0]["keyword"] == "тест"

    def test_record_keyword_increments_frequency(self) -> None:
        self.db.record_keyword("тест")
        self.db.record_keyword("тест")
        top = self.db.get_top_keywords(10)
        assert top[0]["frequency"] >= 2

    def test_empty_keyword_ignored(self) -> None:
        self.db.record_keyword("")
        assert len(self.db.get_top_keywords(10)) == 0

    def test_close(self) -> None:
        self.db.close()

    def teardown_method(self) -> None:
        self.db.close()


class TestTelegramSEOOptimizer:
    def setup_method(self) -> None:
        self.seo = TelegramSEOOptimizer(":memory:")

    def _make_post(self, headline: str = "Test", text: str = "Some content here") -> Post:
        return Post(
            topic="Test",
            headline=headline,
            content=GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text=text,
            ),
            tags=["news"],
        )

    @pytest.mark.asyncio
    async def test_optimize_post_adds_keyword(self) -> None:
        post = self._make_post(headline="News", text="Content about something")
        optimized = await self.seo.optimize_post(post)
        assert "news" in optimized.content.text[:60].lower()

    @pytest.mark.asyncio
    async def test_optimize_post_hashtags(self) -> None:
        post = self._make_post(headline="Test headline", text="Long content for testing")
        optimized = await self.seo.optimize_post(post)
        assert len(optimized.tags) >= 1

    @pytest.mark.asyncio
    async def test_optimize_post_empty_content(self) -> None:
        post = Post(topic="Test", headline="")
        optimized = await self.seo.optimize_post(post)
        assert optimized.tags is not None

    @pytest.mark.asyncio
    async def test_check_indexing_without_telethon(self) -> None:
        post = self._make_post()
        result = await self.seo.check_indexing(post)
        assert result is True

    def test_generate_hashtag_mix_length(self) -> None:
        tags = self.seo._generate_hashtag_mix("test content about news", ["news", "tech"])
        assert 1 <= len(tags) <= 7

    def test_generate_hashtag_mix_includes_brand(self) -> None:
        tags = self.seo._generate_hashtag_mix("test", ["test"])
        assert "вердикт" in tags

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        await self.seo.close()

    def teardown_method(self) -> None:
        import asyncio
        try:
            asyncio.get_event_loop().run_until_complete(self.seo.close())
        except RuntimeError:
            pass


class TestHeadlineScorer:
    def setup_method(self) -> None:
        self.scorer = HeadlineScorer()

    def test_length_score_optimal(self) -> None:
        assert self.scorer._length_score("A" * 50) == 1.0

    def test_length_score_short(self) -> None:
        assert self.scorer._length_score("A" * 5) == 0.0

    def test_digit_score_with_numbers(self) -> None:
        assert self.scorer._digit_score("5 причин почему") > 0

    def test_digit_score_no_numbers(self) -> None:
        assert self.scorer._digit_score("почему это важно") == 0.0

    def test_trigger_score_found(self) -> None:
        assert self.scorer._trigger_score("почему это произошло") >= 0.8

    def test_trigger_score_not_found(self) -> None:
        assert self.scorer._trigger_score("обычный заголовок") <= 0.2

    def test_question_score_question(self) -> None:
        assert self.scorer._question_score("Кто виноват?") == 1.0

    def test_question_score_exclamation(self) -> None:
        assert self.scorer._question_score("Сенсация!") == 0.7

    def test_uniqueness_score_high(self) -> None:
        assert self.scorer._uniqueness_score("разные слова тут") > 0.5

    def test_keyword_score(self) -> None:
        assert self.scorer._keyword_score("россия экономика") > 0.5

    def test_total_score_range(self) -> None:
        s = self.scorer.score("5 причин почему россия изменилась?")
        assert 0.0 <= s <= 1.0


class TestHeadlineSelector:
    def setup_method(self) -> None:
        import tempfile
        self.selector = HeadlineSelector()
        self.selector.history = HeadlineHistory(path=tempfile.mktemp(suffix=".json"))

    def test_select_empty(self) -> None:
        best = self.selector.select([])
        assert best.text == ""

    def test_select_returns_highest_score(self) -> None:
        variants = [
            HeadlineVariant(text="слово", type=HeadlineType.paradox, score=0.0),
            HeadlineVariant(text="5 причин почему экономика россии изменилась?", type=HeadlineType.question, score=0.0),
        ]
        best = self.selector.select(variants)
        assert best.text == "5 причин почему экономика россии изменилась?"

    def test_select_records_history(self) -> None:
        v = HeadlineVariant(text="тестовый заголовок", type=HeadlineType.paradox, score=0.8)
        best = self.selector.select([v])
        assert self.selector.history.is_repeat("тестовый заголовок", threshold_days=365)


class TestHeadlineOptimizer:
    def setup_method(self) -> None:
        self.router = MagicMock()
        self.router.route = AsyncMock()
        self.router.route.return_value = GeneratedContent(
            task_type=TaskType.headline,
            provider=ProviderName.groq,
            text="ПАРАДОКС: Обычное явление | 0.85\nВОПРОС: Почему это важно? | 0.75\nШОК-ФАКТ: Цифры удивят | 0.65",
        )
        self.opt = HeadlineOptimizer(self.router)

    def _make_post(self) -> Post:
        return Post(
            topic="Test topic",
            headline="Original headline",
            content=GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text="Test content",
            ),
            category=PostCategory.ECONOMY,
        )

    @pytest.mark.asyncio
    async def test_generate_variants(self) -> None:
        post = self._make_post()
        variants = await self.opt.generate_variants(post)
        assert len(variants) == 3

    @pytest.mark.asyncio
    async def test_generate_variants_types(self) -> None:
        post = self._make_post()
        variants = await self.opt.generate_variants(post)
        types = {v.type for v in variants}
        assert HeadlineType.paradox in types
        assert HeadlineType.question in types

    def test_parse_variants(self) -> None:
        text = "ПАРАДОКС: Тест | 0.9\nВОПРОС: Тест? | 0.8"
        variants = self.opt._parse_variants(text)
        assert len(variants) == 2

    def test_parse_variants_no_score(self) -> None:
        text = "ПАРАДОКС: Просто тест"
        variants = self.opt._parse_variants(text)
        assert len(variants) == 1
        assert variants[0].score > 0

    def test_build_prompt_includes_category(self) -> None:
        post = self._make_post()
        prompt = self.opt._build_prompt(post)
        assert "economy" in prompt

    @pytest.mark.asyncio
    async def test_optimize_updates_headline(self) -> None:
        post = self._make_post()
        optimized = await self.opt.optimize(post)
        assert optimized.headline != "Original headline"
        assert len(optimized.headlines) == 3
