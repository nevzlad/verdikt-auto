"""Tests for parser module."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.core.models import (
    GeneratedContent,
    HeadlineVariant,
    Post,
    PostCategory,
    ProviderName,
    TaskType,
)
from verdikt_auto.parser.post_parser import HeadlineExtractor, PostParser
from verdikt_auto.parser.validator import ContentValidator


class TestPostParser:
    def setup_method(self) -> None:
        self.parser = PostParser()

    def test_parse_single_post(self) -> None:
        text = (
            "🏆 Заголовок\n\n"
            "🔍 Суть: факты и анализ.\n\n"
            "📊 Контекст: цифры и данные.\n\n"
            "💡 Вывод: прогноз.\n\n"
            "📌 Резюме\n\n"
            "🔗 https://example.com"
        )
        posts = self.parser.parse(text)
        assert len(posts) == 1
        assert posts[0].headline == "Заголовок"

    def test_parse_multi_post(self) -> None:
        text = (
            "## ВЕРДИКТ 1/2\n\n"
            "🔍 Первый пост.\n\n"
            "## ВЕРДИКТ 2/2\n\n"
            "🔍 Второй пост."
        )
        posts = self.parser.parse(text)
        assert len(posts) == 2

    def test_parse_dash_separator(self) -> None:
        text = (
            "🔍 Пост один.\n\n"
            "---\n\n"
            "🔍 Пост два."
        )
        posts = self.parser.parse(text)
        assert len(posts) == 2

    def test_parse_no_blocks_raises(self) -> None:
        text = "Просто текст без эмодзи блоков."
        posts = self.parser.parse(text)
        assert len(posts) == 0

    def test_extract_headline_with_hash(self) -> None:
        text = "# Просто заголовок\n\n🔍 Суть."
        assert self.parser._extract_headline(text) == "Просто заголовок"

    def test_extract_blocks(self) -> None:
        text = (
            "🔍 Суть новости: что произошло.\n\n"
            "📊 Контекст: цифры.\n\n"
            "💡 Вывод."
        )
        blocks = self.parser._extract_blocks(text)
        assert "🔍" in blocks
        assert "📊" in blocks
        assert "💡" in blocks

    def test_detect_category_from_text(self) -> None:
        text = "СВО Украина Донбасс"
        cat = self.parser._detect_category(text)
        assert cat == PostCategory.SVO

    def test_detect_category_tech(self) -> None:
        text = "AI технологии нейросети"
        cat = self.parser._detect_category(text)
        assert cat == PostCategory.TECH

    def test_extract_tags(self) -> None:
        text = "Текст с #новости и #технологии"
        tags = self.parser._extract_tags(text)
        assert "новости" in tags
        assert "технологии" in tags

    def test_extract_bridge(self) -> None:
        text = "Текст\n\n→ Читайте также в нашем канале\n\nЕщё текст"
        bridge = self.parser._extract_bridge(text)
        assert "Читайте" in bridge


class TestHeadlineExtractor:
    def setup_method(self) -> None:
        self.extractor = HeadlineExtractor()

    def test_extract_multiple(self) -> None:
        text = "Первый заголовок\nВторой заголовок\nТретий"
        variants = self.extractor.extract(text)
        assert len(variants) == 3

    def test_score_question(self) -> None:
        score = self.extractor._score_heuristic("Почему это произошло?")
        assert score > 0.5

    def test_score_clickbait_penalty(self) -> None:
        score = self.extractor._score_heuristic("Шок! Сенсация!")
        assert score < 0.5

    def test_detect_type_question(self) -> None:
        assert self.extractor._detect_type("Кто виноват?") == "question"

    def test_detect_type_shock(self) -> None:
        assert self.extractor._detect_type("Сенсация!") == "shock_fact"

    def test_sorted_by_score(self) -> None:
        variants = self.extractor.extract("Обычный заголовок\nПочему это важно?\nШок-контент!")
        assert variants[0].score >= variants[-1].score

    def test_empty_text(self) -> None:
        assert self.extractor.extract("") == []


class TestContentValidator:
    def setup_method(self) -> None:
        self.validator = ContentValidator()

    def _make_post(self, text: str = "A" * 420) -> Post:
        return Post(
            topic="Test",
            headline="Test Headline for Validation",
            tags=["news", "tech"],
            content=GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text=text,
            ),
        )

    def test_valid_post(self) -> None:
        result = self.validator.validate(self._make_post())
        assert result.is_valid

    def test_empty_content(self) -> None:
        post = self._make_post("")
        result = self.validator.validate(post)
        assert not result.is_valid
        assert any("empty" in e.lower() for e in result.errors)

    def test_short_content(self) -> None:
        post = self._make_post("Short")
        result = self.validator.validate(post)
        assert not result.is_valid

    def test_long_content(self) -> None:
        post = self._make_post("A" * 700)
        result = self.validator.validate(post)
        assert not result.is_valid

    def test_clickbait_detection(self) -> None:
        post = self._make_post("Это шок! Сенсация! Невероятные новости! " * 10)
        result = self.validator.validate(post)
        assert result.warnings

    def test_cta_warning(self) -> None:
        post = self._make_post("А" * 450)
        result = self.validator.validate(post)
        cta_warnings = [w for w in result.warnings if "call-to-action" in w.lower()]
        assert len(cta_warnings) > 0

    def test_cta_found(self) -> None:
        post = self._make_post("Подпишись на канал! " + "A" * 400)
        result = self.validator.validate(post)
        cta_warnings = [w for w in result.warnings if "call-to-action" in w.lower()]
        assert len(cta_warnings) == 0

    def test_russian_domain_warning(self) -> None:
        text = "По данным https://tass.ru " + "A" * 400
        post = self._make_post(text)
        result = self.validator.validate(post)
        domain_warnings = [w for w in result.warnings if "tass.ru" in w.lower()]
        assert len(domain_warnings) > 0

    def test_duplicate_detection(self) -> None:
        validator = ContentValidator(published_urls={"abcd1234"})
        post = self._make_post("A" * 420)
        post.content.text = "Duplicate text"
        result = validator.validate(post)
        assert not result.is_valid

    def test_empty_headline(self) -> None:
        post = self._make_post()
        post.headline = ""
        result = self.validator.validate(post)
        assert not result.is_valid

    def test_headline_too_short(self) -> None:
        post = self._make_post()
        post.headline = "Hi"
        result = self.validator.validate(post)
        assert result.is_valid

    def test_long_hashtag_warning(self) -> None:
        post = self._make_post()
        post.tags = ["thisisaverylonghashtagthatistoolong"]
        result = self.validator.validate(post)
        tag_warnings = [w for w in result.warnings if "hashtag" in w.lower()]
        assert len(tag_warnings) > 0

    def test_stale_content_warning(self) -> None:
        post = self._make_post()
        post.created_at = datetime.now() - timedelta(hours=72)
        result = self.validator.validate(post)
        stale_warnings = [w for w in result.warnings if "stale" in w.lower()]
        assert len(stale_warnings) > 0
