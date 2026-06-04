"""Tests for core module (config, exceptions, models, AI router)."""

import pytest

from verdikt_auto.core.config import Settings
from verdikt_auto.core.exceptions import ProviderNotAvailable
from verdikt_auto.core.models import RankedTopic, TaskType, GeneratedContent, ProviderName, RoutingRule
from verdikt_auto.core.ai_router import AIRouter


class TestConfig:
    def test_settings_loads_defaults(self) -> None:
        s = Settings()
        assert s.log_level == "INFO"

    def test_yaml_defaults(self) -> None:
        s = Settings()
        assert s.posts_per_day == 7
        assert s.schedule_hours == [7, 9, 12, 14, 18, 20, 22]


class TestExceptions:
    def test_provider_not_available(self) -> None:
        exc = ProviderNotAvailable("test error")
        assert str(exc) == "test error"
        assert isinstance(exc, Exception)


class TestModels:
    def test_task_type_values(self) -> None:
        assert TaskType.summary.value == "summary"
        assert TaskType.rewrite.value == "rewrite"

    def test_ranked_topic_defaults(self) -> None:
        topic = RankedTopic(topic_id="1", title="Test", score=0.5)
        assert topic.normalized_views == 0.0
        assert topic.relevance_score == 0.0

    def test_generated_content(self) -> None:
        content = GeneratedContent(
            task_type=TaskType.summary,
            provider=ProviderName.groq,
            text="test content",
        )
        assert content.text == "test content"
        assert content.tokens_used == 0


class TestAIRouter:
    @pytest.mark.asyncio
    async def test_no_api_keys_raises_error(self) -> None:
        router = AIRouter(api_keys={})
        with pytest.raises(ProviderNotAvailable):
            await router.route("generate_post", prompt="test prompt")

    def test_get_rule_returns_rule(self) -> None:
        router = AIRouter()
        rule = router._get_rule("generate_post")
        assert isinstance(rule, RoutingRule)
        assert "openrouter" in rule.providers

    def test_get_provider_chain_returns_list(self) -> None:
        router = AIRouter()
        chain = router._get_provider_chain("generate_post")
        assert isinstance(chain, list)
        assert len(chain) > 0
        assert "openrouter" in chain

    def test_routing_map_contains_tasks(self) -> None:
        assert "generate_post" in AIRouter.ROUTING_MAP
        assert "generate_headline" in AIRouter.ROUTING_MAP
