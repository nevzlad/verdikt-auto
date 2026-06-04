"""Comprehensive tests for AI Router module with httpx mocks."""

import json
import time
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from verdikt_auto.core.ai_router import (
    AIRouter,
    BarkClient,
    BaseProvider,
    CacheManager,
    CerebrasClient,
    DeepSeekClient,
    EdgeTTSClient,
    GeminiClient,
    GroqClient,
    HFPipelineClient,
    HuggingFaceImageClient,
    LRUCache,
    MetricsCollector,
    OpenAICompatibleClient,
    OpenRouterClient,
    PersistentCache,
    ReplicateClient,
    SileroClient,
    SklearnClient,
)
from verdikt_auto.core.exceptions import (
    APIError,
    ProviderNotAvailable,
    QuotaExceededError,
    RateLimitError,
    TimeoutError,
)
from verdikt_auto.core.models import CacheEntry, ProviderStats, RoutingRule


# ────────────────────────────────────────────────
#  HELPERS
# ────────────────────────────────────────────────


def mock_chat_response(content: str = "Test response", model: str = "test-model", tokens: int = 42) -> bytes:
    return json.dumps({
        "choices": [{"message": {"content": content}}],
        "model": model,
        "usage": {"total_tokens": tokens},
    }).encode()


def mock_gemini_response(text: str = "Gemini response", tokens: int = 42) -> bytes:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"totalTokenCount": tokens},
    }).encode()


def make_mock_transport(
    status: int = 200,
    body: Optional[bytes] = None,
    content_type: str = "application/json",
) -> httpx.MockTransport:
    if body is None:
        body = mock_chat_response()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


# ────────────────────────────────────────────────
#  CACHE TESTS
# ────────────────────────────────────────────────


class TestLRUCache:
    def test_set_and_get(self) -> None:
        cache = LRUCache(maxsize=10)
        cache.set("key1", "value1", ttl_seconds=3600)
        assert cache.get("key1") == "value1"

    def test_miss_returns_none(self) -> None:
        cache = LRUCache()
        assert cache.get("nonexistent") is None

    def test_eviction(self) -> None:
        cache = LRUCache(maxsize=2)
        cache.set("a", 1, ttl_seconds=3600)
        cache.set("b", 2, ttl_seconds=3600)
        cache.set("c", 3, ttl_seconds=3600)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_ttl_expiry(self) -> None:
        cache = LRUCache()
        cache.set("x", "data", ttl_seconds=0)
        assert cache.get("x") is None

    def test_invalidate(self) -> None:
        cache = LRUCache()
        cache.set("k", "v", ttl_seconds=3600)
        cache.invalidate("k")
        assert cache.get("k") is None

    def test_clear(self) -> None:
        cache = LRUCache()
        cache.set("a", 1, ttl_seconds=3600)
        cache.set("b", 2, ttl_seconds=3600)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None


class TestPersistentCache:
    @pytest.fixture
    def cache(self, tmp_path: Any) -> PersistentCache:
        db = tmp_path / "test_cache.db"
        return PersistentCache(str(db))

    def test_set_and_get(self, cache: PersistentCache) -> None:
        cache.set("k1", {"data": "hello"}, ttl_seconds=3600)
        assert cache.get("k1") == {"data": "hello"}

    def test_miss_returns_none(self, cache: PersistentCache) -> None:
        assert cache.get("missing") is None

    def test_expiry(self, cache: PersistentCache) -> None:
        cache.set("k2", "value", ttl_seconds=0)
        assert cache.get("k2") is None

    def test_cleanup_expired(self, cache: PersistentCache) -> None:
        cache.set("k3", "v1", ttl_seconds=0)
        cache.set("k4", "v2", ttl_seconds=3600)
        deleted = cache.cleanup_expired()
        assert deleted >= 1
        assert cache.get("k4") == "v2"

    def test_override(self, cache: PersistentCache) -> None:
        cache.set("k5", "first", ttl_seconds=3600)
        cache.set("k5", "second", ttl_seconds=3600)
        assert cache.get("k5") == "second"

    def test_close(self, cache: PersistentCache) -> None:
        cache.close()
        cache.close()


class TestCacheManager:
    @pytest.fixture
    def manager(self, tmp_path: Any) -> CacheManager:
        return CacheManager(memory_maxsize=5, db_path=str(tmp_path / "cm.db"))

    def test_set_and_get(self, manager: CacheManager) -> None:
        manager.set("task1", {"a": 1}, "result", ttl_seconds=3600)
        assert manager.get("task1", {"a": 1}) == "result"

    def test_miss_different_kwargs(self, manager: CacheManager) -> None:
        manager.set("task", {"x": 1}, "data", ttl_seconds=3600)
        assert manager.get("task", {"x": 2}) is None

    def test_invalidate(self, manager: CacheManager) -> None:
        manager.set("t", {"k": "v"}, "val", ttl_seconds=3600)
        manager.invalidate("t", {"k": "v"})
        assert manager.get("t", {"k": "v"}) is None

    def test_clear(self, manager: CacheManager) -> None:
        manager.set("a", {}, 1, ttl_seconds=3600)
        manager.set("b", {}, 2, ttl_seconds=3600)
        manager.clear()
        assert manager.get("a", {}) is None

    def test_key_uniqueness(self, manager: CacheManager) -> None:
        manager.set("t", {"q": "hello"}, "r1", ttl_seconds=3600)
        manager.set("t", {"q": "world"}, "r2", ttl_seconds=3600)
        assert manager.get("t", {"q": "hello"}) == "r1"
        assert manager.get("t", {"q": "world"}) == "r2"


# ────────────────────────────────────────────────
#  METRICS COLLECTOR TESTS
# ────────────────────────────────────────────────


class TestMetricsCollector:
    @pytest.fixture
    def mc(self, tmp_path: Any) -> MetricsCollector:
        return MetricsCollector(db_path=str(tmp_path / "metrics.db"))

    def test_record_and_get_stats(self, mc: MetricsCollector) -> None:
        mc.record_request("openrouter", "generate_post", True, 1200, 500)
        mc.record_request("openrouter", "generate_post", True, 800, 300)
        mc.record_request("openrouter", "generate_post", False, 0, 0, "timeout")

        stats = mc.get_provider_stats("openrouter")
        assert stats.total_requests == 3
        assert stats.successful_requests == 2
        assert stats.failed_requests == 1
        assert stats.total_tokens == 800

    def test_percentile_latency(self, mc: MetricsCollector) -> None:
        for i in range(100):
            mc.record_request("groq", "test", True, float(i * 10))
        p50 = mc.get_percentile_latency("groq", "test", 50)
        p95 = mc.get_percentile_latency("groq", "test", 95)
        assert 450 <= p50 <= 550
        assert 940 <= p95 <= 960

    def test_success_rate(self, mc: MetricsCollector) -> None:
        mc.record_request("p", "t", True, 100)
        mc.record_request("p", "t", True, 100)
        mc.record_request("p", "t", False, 0)
        assert mc.get_success_rate("p", "t") == pytest.approx(2 / 3)

    def test_empty_rate(self, mc: MetricsCollector) -> None:
        assert mc.get_success_rate("nonexistent", "x") == 1.0

    def test_record_quota(self, mc: MetricsCollector) -> None:
        mc.record_quota("gemini", 50.0, "2026-12-31T23:59:59")
        stats = mc.get_provider_stats("gemini")
        assert stats.quota_remaining == 50.0

    def test_close(self, mc: MetricsCollector) -> None:
        mc.close()


# ────────────────────────────────────────────────
#  BASE PROVIDER TESTS
# ────────────────────────────────────────────────


class TestBaseProvider:
    def test_has_quota_initially_true(self) -> None:
        class DummyProvider(BaseProvider):
            async def call(self, task: str, **kwargs: Any) -> Any:
                return {"ok": True}

        p = DummyProvider("dummy")
        assert p.has_quota() is True

    def test_mark_unavailable(self) -> None:
        class DummyProvider(BaseProvider):
            async def call(self, task: str, **kwargs: Any) -> Any:
                return {"ok": True}

        p = DummyProvider("dummy")
        p.mark_unavailable(duration_seconds=0.01)
        assert p.has_quota() is False
        time.sleep(0.02)
        assert p.has_quota() is True

    def test_get_usage_stats(self) -> None:
        class DummyProvider(BaseProvider):
            async def call(self, task: str, **kwargs: Any) -> Any:
                return {"ok": True}

        p = DummyProvider("dummy")
        stats = p.get_usage_stats()
        assert stats["name"] == "dummy"
        assert stats["total_requests"] == 0

    @pytest.mark.asyncio
    async def test_call_with_retry_success(self) -> None:
        class GoodProvider(BaseProvider):
            async def call(self, task: str, **kwargs: Any) -> Any:
                return {"result": "ok"}

        p = GoodProvider("good")
        result = await p._call_with_retry(task="test", max_retries=3)
        assert result["result"] == "ok"
        assert "latency_ms" in result
        stats = p.get_usage_stats()
        assert stats["successful_requests"] == 1

    @pytest.mark.asyncio
    async def test_call_with_retry_exhaustion(self) -> None:
        class BadProvider(BaseProvider):
            calls = 0

            async def call(self, task: str, **kwargs: Any) -> Any:
                self.calls += 1
                raise APIError("always fails", status_code=500)

        p = BadProvider("bad")
        with pytest.raises(ProviderNotAvailable):
            await p._call_with_retry(task="test", max_retries=2, base_delay=0.01)
        assert p.calls == 2

    @pytest.mark.asyncio
    async def test_quota_exceeded_not_retried(self) -> None:
        class QuotaProvider(BaseProvider):
            calls = 0

            async def call(self, task: str, **kwargs: Any) -> Any:
                self.calls += 1
                raise QuotaExceededError("no quota")

        p = QuotaProvider("quota")
        with pytest.raises(QuotaExceededError):
            await p._call_with_retry(task="test", max_retries=3, base_delay=0.01)
        assert p.calls == 1

    @pytest.mark.asyncio
    async def test_rate_limit_retries(self) -> None:
        class RateLimitedProvider(BaseProvider):
            calls = 0

            async def call(self, task: str, **kwargs: Any) -> Any:
                self.calls += 1
                if self.calls < 3:
                    raise RateLimitError("too fast")
                return {"ok": True}

        p = RateLimitedProvider("ratelimit")
        result = await p._call_with_retry(task="test", max_retries=3, base_delay=0.01)
        assert result["ok"] is True
        assert "latency_ms" in result
        assert p.calls == 3

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self) -> None:
        class ClientErrorProvider(BaseProvider):
            calls = 0

            async def call(self, task: str, **kwargs: Any) -> Any:
                self.calls += 1
                raise APIError("bad request", status_code=400)

        p = ClientErrorProvider("client_err")
        with pytest.raises(APIError):
            await p._call_with_retry(task="test", max_retries=3, base_delay=0.01)
        assert p.calls == 1


# ────────────────────────────────────────────────
#  LLM CLIENT TESTS (via httpx.MockTransport)
# ────────────────────────────────────────────────


class TestOpenAICompatibleClient:
    @pytest.mark.asyncio
    async def test_successful_call(self) -> None:
        transport = make_mock_transport(200, mock_chat_response("Hello world", "gpt-test", 10))
        async with OpenRouterClient(api_key="test-key") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="test", prompt="Hi", model="gpt-test")
        assert result["text"] == "Hello world"
        assert result["tokens_used"] == 10
        assert result["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_rate_limit_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        transport = httpx.MockTransport(handler)
        async with OpenRouterClient(api_key="key") as client:
            client._client = httpx.AsyncClient(transport=transport)
            with pytest.raises(RateLimitError):
                await client.call(task="test", prompt="hello")

    @pytest.mark.asyncio
    async def test_quota_exceeded_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402)

        transport = httpx.MockTransport(handler)
        async with OpenRouterClient(api_key="key") as client:
            client._client = httpx.AsyncClient(transport=transport)
            with pytest.raises(QuotaExceededError):
                await client.call(task="test", prompt="hello")

    @pytest.mark.asyncio
    async def test_server_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"overloaded")

        transport = httpx.MockTransport(handler)
        async with OpenRouterClient(api_key="key") as client:
            client._client = httpx.AsyncClient(transport=transport)
            with pytest.raises(APIError) as exc_info:
                await client.call(task="test", prompt="hello")
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        transport = make_mock_transport()
        async with GroqClient(api_key="test") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="x", prompt="ping")
            assert result["text"] == "Test response"


class TestGeminiClient:
    @pytest.mark.asyncio
    async def test_successful_call(self) -> None:
        body = mock_gemini_response("Gemini answer", 25)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        transport = httpx.MockTransport(handler)
        async with GeminiClient(api_key="gkey") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="test", prompt="analyze this")
        assert result["text"] == "Gemini answer"
        assert result["tokens_used"] == 25

    @pytest.mark.asyncio
    async def test_rate_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        transport = httpx.MockTransport(handler)
        async with GeminiClient(api_key="gk") as client:
            client._client = httpx.AsyncClient(transport=transport)
            with pytest.raises(RateLimitError):
                await client.call(task="test", prompt="hello")


class TestCerebrasClient:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        transport = make_mock_transport(200, mock_chat_response("Cerebras result", "llama3.1-70b"))
        async with CerebrasClient(api_key="ck") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="headline", prompt="make a headline")
        assert "Cerebras" in result["text"]


class TestDeepSeekClient:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        transport = make_mock_transport(200, mock_chat_response("DeepSeek answer"))
        async with DeepSeekClient(api_key="dk") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="test", prompt="hello")
        assert result["text"] == "DeepSeek answer"


# ────────────────────────────────────────────────
#  IMAGE PROVIDER TESTS
# ────────────────────────────────────────────────




class TestReplicateClient:
    @pytest.mark.asyncio
    async def test_prediction_flow(self) -> None:
        step = {"value": 0}
        outputs = [b"replicate-image-data"]

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal step
            if request.method == "POST":
                step["value"] = 1
                return httpx.Response(201, content=json.dumps({"id": "pred-123"}).encode())
            if step["value"] == 1:
                step["value"] = 2
                return httpx.Response(200, content=json.dumps({
                    "status": "succeeded",
                    "output": ["https://example.com/img.png"],
                }).encode())
            return httpx.Response(200, content=outputs[0])

        transport = httpx.MockTransport(handler)
        async with ReplicateClient(api_key="rk") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="img", prompt="test image")
        assert result["image_bytes"] == b"replicate-image-data"


class TestHuggingFaceImageClient:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"hf-image-data", headers={"content-type": "image/png"})

        transport = httpx.MockTransport(handler)
        async with HuggingFaceImageClient(api_key="hfkey") as client:
            client._client = httpx.AsyncClient(transport=transport)
            result = await client.call(task="img", prompt="a cat")
        assert result["image_bytes"] == b"hf-image-data"

    @pytest.mark.asyncio
    async def test_model_loading(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"Model is loading")

        transport = httpx.MockTransport(handler)
        async with HuggingFaceImageClient(api_key="hfkey") as client:
            client._client = httpx.AsyncClient(transport=transport)
            with pytest.raises(QuotaExceededError):
                await client.call(task="img", prompt="test")


# ────────────────────────────────────────────────
#  TTS PROVIDER TESTS
# ────────────────────────────────────────────────


class TestEdgeTTSClient:
    @pytest.mark.asyncio
    async def test_call_without_edge_tts(self) -> None:
        async with EdgeTTSClient() as client:
            with pytest.raises(ProviderNotAvailable):
                await client.call(task="tts", text="hello")

    def test_default_voice(self) -> None:
        client = EdgeTTSClient()
        assert client.default_voice == "ru-RU-DmitryNeural"


class TestSileroClient:
    def test_default_voice(self) -> None:
        client = SileroClient()
        assert client.default_voice == "ru_v3_xenia"

    @pytest.mark.asyncio
    async def test_call_without_torch(self) -> None:
        with patch.dict("sys.modules", {"torch": None, "omegaconf": None}):
            async with SileroClient() as client:
                with pytest.raises(ProviderNotAvailable):
                    await client.call(task="tts", text="hello")


class TestBarkClient:
    def test_default_voice(self) -> None:
        client = BarkClient()
        assert client.default_voice == "v2/ru_speaker_1"

    @pytest.mark.asyncio
    async def test_call_without_transformers(self) -> None:
        async with BarkClient() as client:
            with pytest.raises(ProviderNotAvailable):
                await client.call(task="tts", text="test")


# ────────────────────────────────────────────────
#  ANALYTICS PROVIDER TESTS
# ────────────────────────────────────────────────


class TestHFPipelineClient:
    @pytest.mark.asyncio
    async def test_call_without_transformers(self) -> None:
        async with HFPipelineClient() as client:
            with pytest.raises(ProviderNotAvailable):
                await client.call(task="classify", text="test")

    def test_name(self) -> None:
        client = HFPipelineClient()
        assert client.name == "hf_pipeline"


class TestSklearnClient:
    @pytest.mark.asyncio
    async def test_call_without_sklearn(self) -> None:
        with patch.dict("sys.modules", {"sklearn": None, "sklearn.feature_extraction.text": None, "sklearn.cluster": None, "sklearn.linear_model": None}):
            async with SklearnClient() as client:
                with pytest.raises(ProviderNotAvailable):
                    await client.call(task="cluster", texts=["hello"])

    def test_name(self) -> None:
        client = SklearnClient()
        assert client.name == "local_sklearn"


# ────────────────────────────────────────────────
#  AI ROUTER INTEGRATION TESTS
# ────────────────────────────────────────────────


class TestAIRouter:
    @pytest.fixture
    def router(self, tmp_path: Any) -> AIRouter:
        return AIRouter(
            api_keys={
                "openrouter": "or_key",
                "groq": "groq_key",
                "cerebras": "cb_key",
            },
            db_path=str(tmp_path / "data"),
        )

    def test_routing_map_defaults(self) -> None:
        rule = AIRouter.ROUTING_MAP["generate_post"]
        assert "openrouter" in rule.providers
        assert rule.max_tokens == 16000

    def test_get_rule_known(self) -> None:
        router = AIRouter()
        rule = router._get_rule("generate_headline")
        assert rule.default_model == "llama3.1-70b"

    def test_get_rule_unknown_uses_default(self) -> None:
        router = AIRouter()
        rule = router._get_rule("unknown_task")
        assert "openrouter" in rule.providers

    def test_get_provider_chain(self) -> None:
        router = AIRouter()
        chain = router._get_provider_chain("generate_post")
        assert chain == ["openrouter", "groq", "gemini", "deepseek"]

    def test_get_provider_chain_local_first(self) -> None:
        router = AIRouter()
        chain = router._get_provider_chain("classify_topic")
        assert chain.index("hf_pipeline") < chain.index("local_sklearn")

    def test_get_provider_chain_custom(self) -> None:
        router = AIRouter()
        chain = router._get_provider_chain("whatever", custom_chain=["groq", "openrouter"])
        assert chain == ["groq", "openrouter"]

    @pytest.mark.asyncio
    async def test_route_fallback_chain(self, router: AIRouter) -> None:
        call_order: list[str] = []

        class TrackedProvider(BaseProvider):
            def __init__(self, name: str, fail: bool = False) -> None:
                super().__init__(name)
                self._fail = fail

            async def call(self, task: str, **kwargs: Any) -> Any:
                call_order.append(self.name)
                if self._fail:
                    raise RateLimitError("rate limited")
                return {"text": f"ok from {self.name}", "model": self.name, "tokens_used": 10, "provider": self.name}

        router._providers["openrouter"] = TrackedProvider("openrouter", fail=True)
        router._providers["groq"] = TrackedProvider("groq", fail=False)

        result = await router.route("generate_post", prompt="hello")
        assert result["text"] == "ok from groq"
        assert call_order[0] == "openrouter"
        assert "groq" in call_order
        assert call_order.count("openrouter") <= 3

    @pytest.mark.asyncio
    async def test_caching_works(self, router: AIRouter) -> None:
        transport = make_mock_transport(200, mock_chat_response("Cached response"))
        groq = router._providers["groq"]
        groq._client = httpx.AsyncClient(transport=transport)

        call_count = 0
        original_call = groq.call

        async def tracking_call(task: str, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return await original_call(task, **kwargs)

        groq.call = tracking_call  # type: ignore[assignment]

        result1 = await router.route("generate_post", prompt="cache me")
        assert result1 is not None

        # Second call with same params should hit cache
        result2 = await router.route("generate_post", prompt="cache me")
        assert result2 is not None

        assert call_count == 1, "Second call should not hit the provider"

    @pytest.mark.asyncio
    async def test_all_providers_exhausted(self, router: AIRouter) -> None:
        # Set all providers to fail
        for name in ("openrouter", "groq", "gemini", "deepseek"):
            p = router._providers.get(name)
            if p and hasattr(p, '_client') and p._client is None:
                pass

        class FailingProvider(BaseProvider):
            async def call(self, task: str, **kwargs: Any) -> Any:
                raise APIError("fail", status_code=500)

        # Replace all relevant providers
        for name in list(router._providers.keys()):
            if name in ("openrouter", "groq", "gemini", "deepseek"):
                fp = FailingProvider(name)
                router._providers[name] = fp

        with pytest.raises(ProviderNotAvailable):
            await router.route("generate_post", prompt="will fail")

    def test_get_best_provider(self, router: AIRouter) -> None:
        router._metrics.record_request("cerebras", "generate_headline", True, 100)
        router._metrics.record_request("cerebras", "generate_headline", True, 50)
        router._metrics.record_request("groq", "generate_headline", False, 0)
        best = router.get_best_provider("generate_headline")
        assert best == "cerebras"

    def test_update_routing(self) -> None:
        router = AIRouter()
        router.update_routing("generate_post", ["groq", "openrouter"])
        rule = router.ROUTING_MAP["generate_post"]
        assert rule.providers == ["groq", "openrouter"]

    @pytest.mark.asyncio
    async def test_health_check(self, router: AIRouter) -> None:
        result = await router.health_check()
        assert "openrouter" in result
        assert "groq" in result
        assert isinstance(result, dict)

    def test_provider_stats(self, router: AIRouter) -> None:
        stats = router.get_provider_stats()
        assert isinstance(stats, dict)

    def test_close(self, router: AIRouter) -> None:
        router.close()

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        router = AIRouter()
        async with router as r:
            assert r is router
        # Should not raise after exit

    @pytest.mark.asyncio
    async def test_call_with_fallback_custom_chain(self, router: AIRouter) -> None:
        transport = make_mock_transport(200, mock_chat_response("Custom chain"))
        deepseek = router._providers["deepseek"]
        deepseek._client = httpx.AsyncClient(transport=transport)

        result = await router.call_with_fallback(
            task="generate_post",
            providers_chain=["deepseek"],
            prompt="custom chain test",
        )
        assert result["text"] == "Custom chain"

    def test_routing_map_contains_all_tasks(self) -> None:
        assert "generate_post" in AIRouter.ROUTING_MAP
        assert "analyze_chat" in AIRouter.ROUTING_MAP
        assert "generate_headline" in AIRouter.ROUTING_MAP
        assert "classify_topic" in AIRouter.ROUTING_MAP
        assert "generate_image" in AIRouter.ROUTING_MAP
        assert "generate_tts" in AIRouter.ROUTING_MAP

    def test_routing_rule_defaults(self) -> None:
        rule = RoutingRule(providers=["a", "b"])
        assert rule.max_tokens == 2048
        assert rule.temperature == 0.7
        assert rule.use_local_first is False


# ────────────────────────────────────────────────
#  EDGE CASE & QUOTA TESTS
# ────────────────────────────────────────────────


class TestQuotaHandling:
    @pytest.mark.asyncio
    async def test_provider_marked_unavailable_on_quota(self, tmp_path: Any) -> None:
        router = AIRouter(db_path=str(tmp_path / "qdata"))

        class QuotaFaker(BaseProvider):
            async def call(self, task: str, **kwargs: Any) -> Any:
                raise QuotaExceededError("no quota left")

        router._providers["openrouter"] = QuotaFaker("openrouter")
        router._providers["groq"] = QuotaFaker("groq")

        with pytest.raises(ProviderNotAvailable):
            await router.route("generate_post", prompt="test")

        assert router._providers["openrouter"].has_quota() is False

    @pytest.mark.asyncio
    async def test_provider_recovers_after_timeout(self) -> None:
        class RecoveringProvider(BaseProvider):
            def __init__(self) -> None:
                super().__init__("recover")
                self._unavailable_until = time.monotonic() - 1

            async def call(self, task: str, **kwargs: Any) -> Any:
                return {"recovered": True}

        p = RecoveringProvider()
        assert p.has_quota() is True
        result = await p.call(task="test")
        assert result["recovered"] is True


class TestRateLimitEdgeCases:
    @pytest.mark.asyncio
    async def test_provider_without_api_key_skipped(self) -> None:
        router = AIRouter(api_keys={})
        with pytest.raises(ProviderNotAvailable):
            await router.route("generate_post", prompt="no keys")

    def test_unknown_provider_in_chain_skipped(self) -> None:
        router = AIRouter()
        # Should not raise
        chain = router._get_provider_chain("generate_post")
        assert "nonexistent" not in chain


class TestTimeoutError:
    def test_is_exception(self) -> None:
        e = TimeoutError("timed out")
        assert isinstance(e, Exception)
        assert str(e) == "timed out"
