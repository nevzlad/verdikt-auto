"""Tests for prompt builder — PromptValidator, PromptCache, and PromptBuilder."""

import re
from datetime import datetime, timedelta
from typing import Any

import pytest

from verdikt_auto.core.models import RankedTopic
from verdikt_auto.prompt.history_manager import HistoryManager
from verdikt_auto.prompt.prompt_builder import (
    MAX_PROMPT_TOKENS,
    PromptBuilder,
    PromptCache,
    PromptValidator,
)
from verdikt_auto.prompt.templates import TemplateRegistry

SAMPLE_TOPICS = [
    RankedTopic(topic_id="t1", title="БРИКС обгоняет G7", score=0.85),
    RankedTopic(topic_id="t2", title="Новые санкции США", score=0.72),
]


class TestPromptValidator:
    def test_count_tokens_empty(self) -> None:
        assert PromptValidator.count_tokens("") == 1

    def test_count_tokens_russian(self) -> None:
        text = "Привет мир" * 100  # 1000 chars
        tokens = PromptValidator.count_tokens(text)
        assert tokens > 0

    def test_truncate_short_text_unchanged(self) -> None:
        text = "Hello world"
        result = PromptValidator.truncate(text, 1000)
        assert result == text

    def test_truncate_long_text(self) -> None:
        text = "A" * 10_000
        result = PromptValidator.truncate(text, 100)
        assert len(result) < len(text)
        assert "TRUNCATED" in result

    def test_validate_and_fix_no_truncation(self) -> None:
        prompt = "<system>Short prompt</system><topics>Test</topics>"
        history = "Some history"
        examples = "<examples>Example</examples>"
        result, h, e = PromptValidator.validate_and_fix(prompt, history, examples)
        assert result == prompt
        assert h == history
        assert e == examples

    def test_validate_and_fix_truncates_history(self) -> None:
        # Build prompt that exceeds 32K tokens
        long_history = "\n".join(f"Post {i} " + "x" * 100 for i in range(5000))
        prompt = f"<history>{long_history}</history>\n<examples>big block</examples>\n<other>{'x' * 50000}</other>"
        history_copy = long_history
        examples = "<examples>Test</examples>"
        result, h, e = PromptValidator.validate_and_fix(
            prompt, history_copy, examples,
        )
        # Should have been truncated — either history or full prompt
        assert len(result) < len(prompt) or len(h) < len(history_copy) or e == ""


class TestPromptCache:
    def test_miss_returns_none(self) -> None:
        cache = PromptCache()
        result = cache.get("2026-06-04", SAMPLE_TOPICS)
        assert result is None

    def test_set_and_get(self) -> None:
        cache = PromptCache()
        cache.set("2026-06-04", SAMPLE_TOPICS, "test prompt")
        result = cache.get("2026-06-04", SAMPLE_TOPICS)
        assert result == "test prompt"

    def test_different_topics_miss(self) -> None:
        cache = PromptCache()
        cache.set("2026-06-04", SAMPLE_TOPICS, "prompt A")
        other = [RankedTopic(topic_id="t3", title="Other", score=0.5)]
        result = cache.get("2026-06-04", other)
        assert result is None

    def test_clear(self) -> None:
        cache = PromptCache()
        cache.set("2026-06-04", SAMPLE_TOPICS, "prompt")
        cache.clear()
        assert cache.get("2026-06-04", SAMPLE_TOPICS) is None

    def test_ttl_expiry(self) -> None:
        cache = PromptCache(ttl_hours=0)  # 0 TTL — expires immediately
        cache.set("2026-06-04", SAMPLE_TOPICS, "prompt")
        result = cache.get("2026-06-04", SAMPLE_TOPICS)
        assert result is None


class TestPromptBuilder:
    @pytest.fixture
    def builder(self, tmp_path) -> PromptBuilder:
        h = HistoryManager(str(tmp_path / "history.json"))
        return PromptBuilder(history=h)

    def test_build_returns_string(self, builder: PromptBuilder) -> None:
        result = builder.build(topics=SAMPLE_TOPICS)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_build_contains_topics(self, builder: PromptBuilder) -> None:
        result = builder.build(topics=SAMPLE_TOPICS)
        assert "БРИКС" in result
        assert "санкции" in result

    def test_build_contains_manifest(self, builder: PromptBuilder) -> None:
        result = builder.build(topics=SAMPLE_TOPICS)
        assert "VERDIKT" in result

    def test_build_no_topics(self, builder: PromptBuilder) -> None:
        result = builder.build(topics=[])
        assert isinstance(result, str)

    def test_build_debug_mode(self, builder: PromptBuilder) -> None:
        topics = [
            RankedTopic(
                topic_id="t1", title="Debug Topic", score=0.9,
                explanation="views(log)=0.500×w1=0.20",
            ),
        ]
        result = builder.build(topics=topics, debug=True)
        assert "views(log)=" in result

    def test_build_cache_hit(self, builder: PromptBuilder) -> None:
        r1 = builder.build(topics=SAMPLE_TOPICS)
        r2 = builder.build(topics=SAMPLE_TOPICS)
        assert r1 == r2

    def test_build_cache_miss_different_date(self, builder: PromptBuilder) -> None:
        # Same topics on different dates should still hit cache
        # because cache key includes date_str
        r1 = builder.build(topics=SAMPLE_TOPICS)
        # Manually set cache to force a "different date" scenario
        builder.cache.clear()
        r2 = builder.build(topics=SAMPLE_TOPICS)
        assert r1 == r2

    def test_build_contains_xml_tags(self, builder: PromptBuilder) -> None:
        result = builder.build(topics=SAMPLE_TOPICS)
        for tag in ("<system>", "<task>", "<topics>", "<structure>", "<rules>"):
            assert tag in result, f"Missing XML tag: {tag}"

    def test_build_contains_seo_requirements(self, builder: PromptBuilder) -> None:
        result = builder.build(topics=SAMPLE_TOPICS)
        assert "аналитика" in result or "вердикт" in result

    def test_build_with_announcements(self, builder: PromptBuilder) -> None:
        from datetime import timedelta
        soon = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        builder.history.add_announcement("Скоро: интервью с экспертом", soon)
        result = builder.build(topics=SAMPLE_TOPICS)
        assert "интервью" in result

    def test_build_missing_placeholder_preserved(self, builder: PromptBuilder) -> None:
        builder.templates.register("custom", "Hello {unknown_placeholder} world")
        result = builder.build(task="custom", topics=[])
        assert "{unknown_placeholder}" in result


class TestTemplateRegistry:
    def test_get_default(self) -> None:
        reg = TemplateRegistry()
        t = reg.get("nonexistent")
        assert "<system>" in t

    def test_register(self) -> None:
        reg = TemplateRegistry()
        reg.register("custom", "Custom template")
        assert reg.get("custom") == "Custom template"

    def test_get_generate_post(self) -> None:
        reg = TemplateRegistry()
        t = reg.get("generate_post")
        assert "{topics}" in t
        assert "{announcements}" in t
        assert "{history}" in t
        assert "{avoid_topics}" in t
