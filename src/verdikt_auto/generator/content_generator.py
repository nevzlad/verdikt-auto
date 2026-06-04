"""Content generator — orchestrates AI-driven post creation via router."""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import (
    GeneratedContent,
    HeadlineType,
    HeadlineVariant,
    PostCategory,
    RankedTopic,
    TaskType,
)
from verdikt_auto.prompt.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    text: str
    model: str = ""
    tokens_used: int = 0
    generation_time: float = 0.0


class ContentGenerator:
    """Generates post components using AI Router with auto-regeneration."""

    MAX_REGENERATE_ATTEMPTS = 3

    def __init__(self, router: AIRouter, prompt_builder: Optional[PromptBuilder] = None) -> None:
        self.router = router
        self.prompt_builder = prompt_builder or PromptBuilder()

    async def generate_post(self, topic: RankedTopic, prompt: str) -> GenerationResult:
        """Generate a full post via router, validate blocks, auto-regenerate on failure."""
        for attempt in range(1, self.MAX_REGENERATE_ATTEMPTS + 1):
            result = await self.router.route(
                "generate_post",
                prompt=prompt,
                topic_title=topic.title,
                topic_keywords=topic.explanation,
            )
            text = result.text if isinstance(result, GeneratedContent) else str(result)
            if self._validate_post_blocks(text):
                return GenerationResult(
                    text=text,
                    model=getattr(result, "model", ""),
                    tokens_used=getattr(result, "tokens_used", 0),
                    generation_time=getattr(result, "generation_time", 0.0),
                )
            logger.warning("Post validation failed (attempt %d/%d)", attempt, self.MAX_REGENERATE_ATTEMPTS)
        return GenerationResult(text=text)

    async def generate_headlines(self, post_text: str, category: PostCategory = PostCategory.GENERAL) -> list[HeadlineVariant]:
        """Generate 3 headline variants (paradox, question, shock-fact) with scores."""
        prompt = (
            f"Создай 3 варианта заголовка для этого поста (категория: {category.value}):\n\n"
            f"{post_text[:1500]}\n\n"
            "Формат:\n"
            "ПАРАДОКС: <заголовок>\n"
            "ВОПРОС: <заголовок>\n"
            "ШОК-ФАКТ: <заголовок>\n\n"
            "После каждого через | укажи оценку от 0.0 до 1.0"
        )
        result = await self.router.route("generate_headline", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return self._parse_headline_variants(text)

    async def generate_image_prompt(self, post_text: str, category: PostCategory = PostCategory.GENERAL) -> str:
        """Generate an image generation prompt based on post content, style per category."""
        style_map = {
            PostCategory.SVO: "реалистичный стиль, новостная фотография",
            PostCategory.MIDEAST: "реалистичный стиль, новостная фотография, ближневосточный контекст",
            PostCategory.USA: "реалистичный стиль, американский контекст",
            PostCategory.ECONOMY: "деловой стиль, инфографика, графики",
            PostCategory.TECH: "футуристический стиль, киберпанк, технологии",
            PostCategory.GENERAL: "нейтральный новостной стиль",
        }
        style = style_map.get(category, "нейтральный новостной стиль")
        prompt = (
            f"Сгенерируй промпт для создания изображения по этому посту.\n"
            f"Стиль: {style}\n\n"
            f"Текст поста:\n{post_text[:1000]}\n\n"
            "Верни только промпт на английском, 1-2 предложения."
        )
        result = await self.router.route("generate_image_prompt", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return text.strip().strip('"').strip("'")

    async def generate_tts_text(self, post_text: str) -> str:
        """Extract and adapt post content for TTS — 30-45 sec reading time."""
        prompt = (
            "Адаптируй этот текст для озвучки голосом (30-45 секунд чтения, ~100-150 слов).\n"
            "Сохрани: крюк (hook), ключевой факт, прогноз.\n"
            "Убери: сложные числа, сноски, списки, эмодзи, хештеги, URL.\n"
            "Сделай: разговорный стиль, паузы между частями.\n\n"
            f"Текст:\n{post_text[:2000]}"
        )
        result = await self.router.route("generate_tts", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return self._clean_tts_text(text)

    def _validate_post_blocks(self, text: str) -> bool:
        """Check that the generated post has all required emoji-marked blocks."""
        required_blocks = ["🔍", "📊", "💡", "📌", "🔗"]
        found = sum(1 for emoji in required_blocks if emoji in text)
        return found >= 3

    def _parse_headline_variants(self, text: str) -> list[HeadlineVariant]:
        """Parse headline variants from LLM response."""
        variants: list[HeadlineVariant] = []
        type_map = {
            "ПАРАДОКС": HeadlineType.paradox,
            "ВОПРОС": HeadlineType.question,
            "ШОК-ФАКТ": HeadlineType.shock_fact,
        }
        for line in text.strip().split("\n"):
            line = line.strip()
            for prefix, htype in type_map.items():
                if line.upper().startswith(prefix):
                    rest = line[len(prefix):].lstrip(":").strip()
                    if "|" in rest:
                        headline_text, score_str = rest.rsplit("|", 1)
                        score = float(score_str.strip())
                    else:
                        headline_text, score = rest, 0.0
                    headline_text = headline_text.strip().strip('"').strip("'")
                    if headline_text:
                        variants.append(HeadlineVariant(text=headline_text, type=htype, score=score))
                    break
        return variants

    def _clean_tts_text(self, text: str) -> str:
        """Remove emojis, markdown, URLs, hashtags from TTS text."""
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"#\w+", "", text)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+"
        )
        text = emoji_pattern.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
