"""Tests for formatter module: ImageGenerator, TTSGenerator, TextFormatter."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.core.models import GeneratedContent, Post, PostCategory, ProviderName, TaskType
from verdikt_auto.formatter.image_generator import CATEGORY_TEMPLATES, FALLBACK_BG_COLORS, ImageGenerator
from verdikt_auto.formatter.text_formatter import TextFormatter
from verdikt_auto.formatter.tts_generator import TTSGenerator


@pytest.fixture
def mock_router() -> MagicMock:
    router = MagicMock()
    router.route = AsyncMock()
    return router


class TestImageGenerator:
    def setup_method(self) -> None:
        self.router = MagicMock()
        self.router.route = AsyncMock()
        self.gen = ImageGenerator(self.router)
        self.gen._pillow_available = False

    @pytest.mark.asyncio
    async def test_cat_has_templates(self) -> None:
        assert PostCategory.TECH in CATEGORY_TEMPLATES
        assert "киберпанк" in CATEGORY_TEMPLATES[PostCategory.TECH]

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        prompt = "test prompt"
        cache_path = self.gen._cache_path(prompt)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("fake")
        result = await self.gen.generate_image(prompt)
        assert result == str(cache_path)
        cache_path.unlink()

    @pytest.mark.asyncio
    async def test_generate_returns_none_on_failure(self) -> None:
        self.router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_image,
            provider=ProviderName.replicate,
            text="",
        )
        result = await self.gen.generate_image("test prompt", PostCategory.TECH)
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_path_consistency(self) -> None:
        p1 = self.gen._cache_path("hello")
        p2 = self.gen._cache_path("hello")
        assert p1 == p2

    def test_extract_title(self) -> None:
        prompt = "Some long title for testing\nmore text"
        title = self.gen._extract_title(prompt)
        assert title == "Some long title for testing"

    def test_extract_title_empty(self) -> None:
        assert self.gen._extract_title("") == ""

    def test_fallback_colors_all_categories(self) -> None:
        for cat in PostCategory:
            assert cat in FALLBACK_BG_COLORS

    def test_wrap_text(self) -> None:
        wrapped = self.gen._wrap_text("a b c d e", 3)
        assert len(wrapped) >= 3


class TestTTSGenerator:
    def setup_method(self) -> None:
        self.router = MagicMock()
        self.router.route = AsyncMock()
        self.router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_tts,
            provider=ProviderName.edge_tts,
            text="test audio text",
        )
        self.tts = TTSGenerator(self.router)
        self.tts._ffmpeg_available = False

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self) -> None:
        result = await self.tts.generate_ogg("")
        assert result is None

    @pytest.mark.asyncio
    async def test_adapt_removes_urls(self) -> None:
        adapted = self.tts._adapt_for_tts("Text https://example.com more")
        assert "https://" not in adapted

    @pytest.mark.asyncio
    async def test_adapt_removes_hashtags(self) -> None:
        adapted = self.tts._adapt_for_tts("Text #tag more")
        assert "#tag" not in adapted

    @pytest.mark.asyncio
    async def test_adapt_truncates_long_text(self) -> None:
        long_text = "word " * 300
        adapted = self.tts._adapt_for_tts(long_text)
        assert len(adapted.split()) <= 130

    def test_cache_dir_created(self) -> None:
        assert self.tts.CACHE_DIR.exists()


class TestTextFormatter:
    def setup_method(self) -> None:
        self.fmt = TextFormatter()

    def _make_post(self, headline: str = "Test Headline", text: str = "Test content here") -> Post:
        return Post(
            topic="Test",
            headline=headline,
            content=GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text=text,
            ),
            tags=["news", "tech"],
            category=PostCategory.TECH,
        )

    def test_format_post_basic(self) -> None:
        post = self._make_post()
        result = self.fmt.format_post(post)
        assert "<b>" in result
        assert "Test content" in result

    def test_format_post_with_hashtags(self) -> None:
        post = self._make_post()
        result = self.fmt.format_post(post)
        assert "#" in result

    def test_format_post_parts_splits_long(self) -> None:
        post = self._make_post(headline="H", text="A" * 5000)
        parts = self.fmt.format_post_parts(post)
        assert len(parts) > 0
        all_len = sum(len(p) for p in parts)
        assert all_len >= 5000

    def test_markdown_to_html_bold(self) -> None:
        assert self.fmt._markdown_to_html("**bold**") == "<b>bold</b>"

    def test_markdown_to_html_italic(self) -> None:
        assert self.fmt._markdown_to_html("*italic*") == "<i>italic</i>"

    def test_markdown_to_html_link(self) -> None:
        result = self.fmt._markdown_to_html("[text](https://example.com)")
        assert '<a href="https://example.com">text</a>' in result

    def test_markdown_to_html_url(self) -> None:
        result = self.fmt._markdown_to_html("See https://example.com/page")
        assert "example.com" in result
        assert "<a href=" in result

    def test_generate_hashtag_mix_length(self) -> None:
        post = self._make_post()
        tags = self.fmt._generate_hashtag_mix(post)
        assert 1 <= len(tags) <= 7

    def test_generate_hashtag_mix_includes_brand(self) -> None:
        post = self._make_post()
        tags = self.fmt._generate_hashtag_mix(post)
        assert "вердикт" in tags

    def test_build_inline_keyboard(self) -> None:
        sources = ["https://example.com/1", "https://example.com/2"]
        result = self.fmt.build_inline_keyboard(sources)
        assert "example.com" in result

    def test_build_inline_keyboard_empty(self) -> None:
        assert self.fmt.build_inline_keyboard([]) == ""

    def test_format_voice_intro(self) -> None:
        result = self.fmt.format_voice_intro("Test", 65)
        assert "1:05" in result
        assert "Аудиоверсия" in result

    def test_split_into_parts(self) -> None:
        long = "A" * 3000 + "\n\n" + "B" * 2000
        parts = self.fmt._split_into_parts(long)
        assert len(parts) >= 2

    def test_empty_post(self) -> None:
        post = Post(topic="", headline="")
        result = self.fmt.format_post(post)
        assert result == ""
