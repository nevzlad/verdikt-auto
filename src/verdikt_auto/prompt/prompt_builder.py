"""Prompt builder — assembles prompts from templates, validates length, caches by date."""

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from verdikt_auto.core.models import RankedTopic
from verdikt_auto.prompt.history_manager import CHANNEL_MANIFEST, HistoryManager
from verdikt_auto.prompt.templates import TemplateRegistry

logger = logging.getLogger(__name__)

MAX_PROMPT_TOKENS = 32_000
TOKEN_RATIO = 4.0  # 1 token ≈ 4 chars for Russian


class PromptValidator:
    """Validates prompt length and truncates if necessary."""

    @staticmethod
    def count_tokens(text: str) -> int:
        return int(len(text) / TOKEN_RATIO) + 1

    @staticmethod
    def truncate(text: str, max_tokens: int) -> str:
        if PromptValidator.count_tokens(text) <= max_tokens:
            return text
        max_chars = int(max_tokens * TOKEN_RATIO)
        return text[:max_chars] + "\n\n# TRUNCATED — prompt exceeded token limit"

    @classmethod
    def validate_and_fix(
        cls,
        prompt: str,
        history_text: str,
        examples_text: str,
    ) -> tuple[str, str, str]:
        """Reduce history and examples if prompt exceeds token limit."""
        current_tokens = cls.count_tokens(prompt)
        if current_tokens <= MAX_PROMPT_TOKENS:
            return prompt, history_text, examples_text

        # First: truncate history by half
        history_lines = history_text.split("\n")
        history_text = "\n".join(history_lines[:max(len(history_lines) // 2, 1)])
        prompt = prompt.replace(
            "\n".join(history_lines),
            history_text,
        ) if len(history_lines) > 1 else prompt

        current_tokens = cls.count_tokens(prompt)
        if current_tokens <= MAX_PROMPT_TOKENS:
            return prompt, history_text, examples_text

        # Second: remove examples
        examples_tag = re.search(r"<examples>.*?</examples>", prompt, re.DOTALL)
        if examples_tag:
            prompt = prompt[:examples_tag.start()] + prompt[examples_tag.end():]
            examples_text = ""

        current_tokens = cls.count_tokens(prompt)
        if current_tokens <= MAX_PROMPT_TOKENS:
            return prompt, history_text, examples_text

        # Final: hard truncate
        prompt = cls.truncate(prompt, MAX_PROMPT_TOKENS)
        return prompt, history_text, examples_text


class PromptCache:
    """Caches rendered prompts by date hash to avoid redundant rebuilds."""

    def __init__(self, ttl_hours: int = 24) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._cache: dict[str, tuple[str, datetime]] = {}

    def _make_key(self, date_str: str, topics_hash: str) -> str:
        return f"{date_str}:{topics_hash}"

    def get(self, date_str: str, topics: list[RankedTopic]) -> Optional[str]:
        topics_hash = hashlib.md5(
            "|".join(t.topic_id for t in topics).encode()
        ).hexdigest()[:12]
        key = self._make_key(date_str, topics_hash)
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_prompt, cached_at = entry
        if datetime.now() - cached_at > self._ttl:
            del self._cache[key]
            return None
        return cached_prompt

    def set(self, date_str: str, topics: list[RankedTopic], prompt: str) -> None:
        topics_hash = hashlib.md5(
            "|".join(t.topic_id for t in topics).encode()
        ).hexdigest()[:12]
        key = self._make_key(date_str, topics_hash)
        self._cache[key] = (prompt, datetime.now())

    def clear(self) -> None:
        self._cache.clear()


class PromptBuilder:
    """Builds prompts for post generation using templates, history, and channel context."""

    def __init__(
        self,
        history: Optional[HistoryManager] = None,
        template_registry: Optional[TemplateRegistry] = None,
    ) -> None:
        self.templates = template_registry or TemplateRegistry()
        self.history = history or HistoryManager()
        self.cache = PromptCache()

    def _format_topics(self, topics: list[RankedTopic], debug: bool = False) -> str:
        lines: list[str] = []
        for i, t in enumerate(topics, 1):
            line = f"  {i}. {t.title} (score={t.score:.4f})"
            if debug and t.explanation:
                line += f" [{t.explanation}]"
            lines.append(line)
        return "\n".join(lines)

    def _format_announcements(self) -> str:
        anns = self.history.get_unresolved_announcements()
        if not anns:
            return "  Нет неразрешённых анонсов"
        return "\n".join(
            f"  - {a['title']} (на {a.get('scheduled_date', 'N/A')})"
            for a in anns
        )

    def _format_history(self, days: int = 14) -> str:
        recent = self.history.get_recent_topics(days=days)
        if not recent:
            return "  Нет истории за последние 14 дней"
        return "\n".join(
            f"  - {p['title']} ({p.get('published_at', '')[:10]})"
            for p in recent[-20:]
        )

    def _format_anti_repeats(self, hours: int = 48) -> str:
        repeats = self.history.get_anti_repeats(hours=hours)
        if not repeats:
            return "  Нет недавно опубликованных тем"
        return "\n".join(f"  - {t[:80]}" for t in repeats[-10:])

    def _format_channel_stats(self) -> str:
        stats = self.history.get_channel_stats()
        return (
            f"  Подписчиков: {stats['subscribers']:,}\n"
            f"  ERR: {stats['err']:.2%}\n"
            f"  Постов за 7 дней: {stats['posts_last_7d']}\n"
            f"  Средние просмотры: {stats['avg_views']:,}"
        )

    def _get_seo_requirements(self) -> str:
        return "Одно из ключевых слов: аналитика, разбор, вердикт, OSINT, фейк"

    def build(
        self,
        task: str = "generate_post",
        topics: Optional[list[RankedTopic]] = None,
        debug: bool = False,
    ) -> str:
        """Assemble a complete prompt by filling the template with context."""
        topics = topics or []
        template = self.templates.get(task)

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday = now.weekday()

        # Check cache
        cached = self.cache.get(date_str, topics)
        if cached is not None:
            logger.debug("Prompt cache hit for %s", date_str)
            return cached

        context = {
            "manifest": CHANNEL_MANIFEST,
            "content_plan": self.history.get_content_plan(weekday),
            "topics": self._format_topics(topics, debug=debug),
            "announcements": self._format_announcements(),
            "history": self._format_history(),
            "avoid_topics": self._format_anti_repeats(),
            "channel_stats": self._format_channel_stats(),
            "seo_requirements": self._get_seo_requirements(),
            "date": date_str,
            "weekday": ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"][weekday],
        }

        prompt = template
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in prompt:
                prompt = prompt.replace(placeholder, value)

        # Validate and truncate if needed
        history_text = self._format_history()
        examples_text = prompt  # will be truncated by validator

        prompt, _, _ = PromptValidator.validate_and_fix(
            prompt, history_text, examples_text,
        )

        # Cache
        self.cache.set(date_str, topics, prompt)
        logger.debug(
            "Prompt built: %d chars (~%d tokens)",
            len(prompt),
            PromptValidator.count_tokens(prompt),
        )
        return prompt
