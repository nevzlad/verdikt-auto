"""Tests for content generator module."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from verdikt_auto.core.models import (
    GeneratedContent,
    HeadlineType,
    HeadlineVariant,
    PostCategory,
    ProviderName,
    RankedTopic,
    TaskType,
)
from verdikt_auto.generator.content_generator import ContentGenerator, GenerationResult


@pytest.fixture
def mock_router() -> MagicMock:
    router = MagicMock()
    router.route = AsyncMock()
    return router


@pytest.fixture
def generator(mock_router: MagicMock) -> ContentGenerator:
    return ContentGenerator(router=mock_router)


@pytest.fixture
def sample_topic() -> RankedTopic:
    return RankedTopic(
        topic_id="test_123",
        title="Test topic title",
        score=0.85,
        explanation="тест, украина, экономика",
    )


class TestGeneratePost:
    @pytest.mark.asyncio
    async def test_generate_post_success(self, generator: ContentGenerator, mock_router: MagicMock, sample_topic: RankedTopic) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="🔍 Суть новости.\n\n📊 Контекст.\n\n💡 Вывод.\n\n📌 Резюме\n\n🔗 Источник",
            model="test-model",
            tokens_used=150,
            generation_time=2.5,
        )
        result = await generator.generate_post(sample_topic, "prompt")
        assert isinstance(result, GenerationResult)
        assert "🔍" in result.text
        assert "📊" in result.text
        assert result.tokens_used == 150

    @pytest.mark.asyncio
    async def test_generate_post_auto_regenerate(self, generator: ContentGenerator, mock_router: MagicMock, sample_topic: RankedTopic) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Текст без блоков",
        )
        result = await generator.generate_post(sample_topic, "prompt")
        assert mock_router.route.call_count == 3

    @pytest.mark.asyncio
    async def test_generate_post_valid_on_second_try(self, generator: ContentGenerator, mock_router: MagicMock, sample_topic: RankedTopic) -> None:
        call_count = 0

        async def _side_effect(*args: object, **kwargs: object) -> GeneratedContent:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return GeneratedContent(
                    task_type=TaskType.generate_post,
                    provider=ProviderName.groq,
                    text="Текст без блоков",
                )
            return GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text="🔍 Суть.\n\n📊 Контекст.\n\n💡 Вывод.\n\n📌 Резюме\n\n🔗 Источник",
            )

        mock_router.route.side_effect = _side_effect
        result = await generator.generate_post(sample_topic, "prompt")
        assert "🔍" in result.text
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_generate_post_invalid_with_line_breaks_in_emojis(self, generator: ContentGenerator, mock_router: MagicMock, sample_topic: RankedTopic) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="🔍Суть\n📊 Контекст.",
        )
        result = await generator.generate_post(sample_topic, "prompt")
        assert mock_router.route.call_count == 3


class TestGenerateHeadlines:
    @pytest.mark.asyncio
    async def test_generate_headlines_success(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.headline,
            provider=ProviderName.groq,
            text=(
                "ПАРАДОКС: Обычное явление, которое изменило всё | 0.85\n"
                "ВОПРОС: Почему никто не ожидал такого поворота? | 0.75\n"
                "ШОК-ФАКТ: Цифры, которые вас удивят | 0.65"
            ),
        )
        variants = await generator.generate_headlines("post text", PostCategory.ECONOMY)
        assert len(variants) == 3
        assert variants[0].type == HeadlineType.paradox
        assert abs(variants[0].score - 0.85) < 0.01

    @pytest.mark.asyncio
    async def test_generate_headlines_empty(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.headline,
            provider=ProviderName.groq,
            text="No valid variants here",
        )
        variants = await generator.generate_headlines("text")
        assert len(variants) == 0


class TestGenerateImagePrompt:
    @pytest.mark.asyncio
    async def test_generate_image_prompt(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.image_prompt,
            provider=ProviderName.groq,
            text="A photorealistic scene of a modern city with futuristic elements",
        )
        result = await generator.generate_image_prompt("post text", PostCategory.TECH)
        assert "A photorealistic" in result

    @pytest.mark.asyncio
    async def test_generate_image_prompt_strips_quotes(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.image_prompt,
            provider=ProviderName.groq,
            text='"A city scene with tech"',
        )
        result = await generator.generate_image_prompt("text", PostCategory.GENERAL)
        assert not result.startswith('"')
        assert not result.endswith('"')

    @pytest.mark.asyncio
    async def test_generate_image_prompt_tech_style(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.image_prompt,
            provider=ProviderName.groq,
            text="Futuristic cyberpunk landscape",
        )
        result = await generator.generate_image_prompt("text", PostCategory.TECH)
        assert "Futuristic" in result


class TestGenerateTTS:
    @pytest.mark.asyncio
    async def test_generate_tts_cleaned(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_tts,
            provider=ProviderName.edge_tts,
            text="Важный факт о экономике. Это изменит всё.",
        )
        result = await generator.generate_tts_text("Some #text with https://url.com and 😊 emoji")
        assert "#text" not in result
        assert "https://" not in result
        assert "😊" not in result

    @pytest.mark.asyncio
    async def test_generate_tts_empty(self, generator: ContentGenerator, mock_router: MagicMock) -> None:
        mock_router.route.return_value = GeneratedContent(
            task_type=TaskType.generate_tts,
            provider=ProviderName.edge_tts,
            text="",
        )
        result = await generator.generate_tts_text("text")
        assert result == ""


class TestValidatePostBlocks:
    def test_validate_passes_with_3_blocks(self, generator: ContentGenerator) -> None:
        text = "🔍 блок\n📊 блок\n💡 блок"
        assert generator._validate_post_blocks(text) is True

    def test_validate_fails_with_2_blocks(self, generator: ContentGenerator) -> None:
        text = "🔍 блок\n📊 блок"
        assert generator._validate_post_blocks(text) is False

    def test_validate_fails_empty(self, generator: ContentGenerator) -> None:
        assert generator._validate_post_blocks("") is False


class TestParseHeadlineVariants:
    def test_parse_variants(self, generator: ContentGenerator) -> None:
        text = "ПАРАДОКС: Тест | 0.9\nВОПРОС: Тест? | 0.8\nШОК-ФАКТ: Тест! | 0.7"
        variants = generator._parse_headline_variants(text)
        assert len(variants) == 3

    def test_parse_missing_score(self, generator: ContentGenerator) -> None:
        text = "ПАРАДОКС: Просто заголовок"
        variants = generator._parse_headline_variants(text)
        assert len(variants) == 1
        assert variants[0].score == 0.0


class TestCleanTTS:
    def test_clean_tts_removes_urls(self, generator: ContentGenerator) -> None:
        result = generator._clean_tts_text("Text https://example.com more")
        assert "https://" not in result

    def test_clean_tts_removes_hashtags(self, generator: ContentGenerator) -> None:
        result = generator._clean_tts_text("Text #tag more")
        assert "#tag" not in result

    def test_clean_tts_removes_emojis(self, generator: ContentGenerator) -> None:
        result = generator._clean_tts_text("Text 😊👍 more")
        assert "😊" not in result
        assert "👍" not in result

    def test_clean_tts_normalizes_spaces(self, generator: ContentGenerator) -> None:
        result = generator._clean_tts_text("Text   with   spaces")
        assert "   " not in result
