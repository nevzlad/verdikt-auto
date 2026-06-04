"""AI Router — multi-provider routing with fallbacks, caching, and metrics."""

import asyncio
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from verdikt_auto.core.exceptions import (
    APIError,
    ProviderNotAvailable,
    ProviderRateLimited,
    QuotaExceededError,
    RateLimitError,
    TimeoutError,
)
from verdikt_auto.core.models import CacheEntry, ProviderStats, RoutingRule

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  IN-MEMORY LRU CACHE
# ──────────────────────────────────────────────


class LRUCache:
    """Thread-safe LRU cache with TTL support."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if datetime.now() > entry.expires_at:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return entry.response

    def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        while len(self._store) >= self._maxsize:
            self._store.popitem(last=False)
        entry = CacheEntry(
            cache_key=key,
            response=value,
            task="",
            expires_at=datetime.now() + timedelta(seconds=ttl_seconds),
        )
        self._store[key] = entry

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# ──────────────────────────────────────────────
#  SQLITE PERSISTENT CACHE
# ──────────────────────────────────────────────


class PersistentCache:
    """SQLite-backed cache for long-term response storage."""

    def __init__(self, db_path: str = "data/ai_cache.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS response_cache (
                cache_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                task TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cache_expires
            ON response_cache(expires_at)
            """
        )
        conn.commit()

    def get(self, key: str) -> Optional[Any]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT response, expires_at FROM response_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() >= expires:
            conn.execute("DELETE FROM response_cache WHERE cache_key = ?", (key,))
            conn.commit()
            return None
        try:
            return json.loads(row["response"])
        except (json.JSONDecodeError, TypeError):
            return None

    def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        conn = self._get_conn()
        expires = (datetime.now() + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT OR REPLACE INTO response_cache (cache_key, response, task, expires_at)
               VALUES (?, ?, ?, ?)""",
            (key, json.dumps(value, ensure_ascii=False, default=str), "", expires),
        )
        conn.commit()

    def cleanup_expired(self) -> int:
        conn = self._get_conn()
        result = conn.execute(
            "DELETE FROM response_cache WHERE expires_at <= datetime('now', 'localtime')"
        )
        conn.commit()
        return result.rowcount

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ──────────────────────────────────────────────
#  CACHE MANAGER (MEMORY + SQLITE)
# ──────────────────────────────────────────────


class CacheManager:
    """Dual-layer cache: fast LRU memory + persistent SQLite."""

    def __init__(self, memory_maxsize: int = 1000, db_path: str = "data/ai_cache.db") -> None:
        self._memory = LRUCache(maxsize=memory_maxsize)
        self._persistent = PersistentCache(db_path=db_path)

    def _make_key(self, task: str, kwargs: dict) -> str:
        raw = f"{task}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=str)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, task: str, kwargs: dict) -> Optional[Any]:
        key = self._make_key(task, kwargs)
        cached = self._memory.get(key)
        if cached is not None:
            logger.debug("Cache HIT (memory): %s", key[:12])
            return cached
        cached = self._persistent.get(key)
        if cached is not None:
            logger.debug("Cache HIT (sqlite): %s", key[:12])
            self._memory.set(key, cached, ttl_seconds=3600)
            return cached
        logger.debug("Cache MISS: %s", key[:12])
        return None

    def set(self, task: str, kwargs: dict, value: Any, ttl_seconds: int = 86400) -> None:
        key = self._make_key(task, kwargs)
        self._memory.set(key, value, ttl_seconds=ttl_seconds)
        self._persistent.set(key, value, ttl_seconds=ttl_seconds)

    def invalidate(self, task: str, kwargs: dict) -> None:
        key = self._make_key(task, kwargs)
        self._memory.invalidate(key)
        conn = self._persistent._get_conn()
        conn.execute("DELETE FROM response_cache WHERE cache_key = ?", (key,))
        conn.commit()

    def clear(self) -> None:
        self._memory.clear()
        conn = self._persistent._get_conn()
        conn.execute("DELETE FROM response_cache")
        conn.commit()

    def close(self) -> None:
        self._persistent.close()


# ──────────────────────────────────────────────
#  METRICS COLLECTOR
# ──────────────────────────────────────────────


class MetricsCollector:
    """Collects and stores provider usage metrics in SQLite."""

    def __init__(self, db_path: str = "data/ai_metrics.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
        # In-memory counters for fast access
        self._counters: dict[str, dict[str, float]] = {}
        self._latencies: dict[str, list[float]] = {}

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS provider_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_name TEXT NOT NULL,
                task TEXT NOT NULL,
                success INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                error_message TEXT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS quota_state (
                provider_name TEXT PRIMARY KEY,
                remaining REAL,
                reset_at TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.commit()

    def record_request(
        self,
        provider: str,
        task: str,
        success: bool,
        latency_ms: float,
        tokens_used: int = 0,
        error_message: Optional[str] = None,
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO provider_metrics
               (provider_name, task, success, latency_ms, tokens_used, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (provider, task, 1 if success else 0, latency_ms, tokens_used, error_message),
        )
        conn.commit()

        key = f"{provider}.{task}"
        c = self._counters.setdefault(key, {"total": 0, "success": 0, "fail": 0, "tokens": 0})
        c["total"] += 1
        if success:
            c["success"] += 1
        else:
            c["fail"] += 1
        c["tokens"] += tokens_used

        self._latencies.setdefault(key, []).append(latency_ms)

    def record_quota(self, provider: str, remaining: float, reset_at: Optional[str] = None) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO quota_state (provider_name, remaining, reset_at)
               VALUES (?, ?, ?)""",
            (provider, remaining, reset_at),
        )
        conn.commit()

    def get_provider_stats(self, provider: str) -> ProviderStats:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   COALESCE(SUM(success), 0) as successes,
                   COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) as failures,
                   COALESCE(AVG(latency_ms), 0) as avg_latency,
                   COALESCE(SUM(tokens_used), 0) as total_tokens
               FROM provider_metrics WHERE provider_name = ?""",
            (provider,),
        ).fetchone()

        quota_row = conn.execute(
            "SELECT remaining FROM quota_state WHERE provider_name = ?",
            (provider,),
        ).fetchone()

        return ProviderStats(
            name=provider,
            total_requests=row[0] if row else 0,
            successful_requests=int(row[1]) if row else 0,
            failed_requests=int(row[2]) if row else 0,
            total_latency_ms=float(row[3]) if row else 0.0,
            total_tokens=int(row[4]) if row else 0,
            quota_remaining=quota_row[0] if quota_row else None,
        )

    def get_percentile_latency(self, provider: str, task: str, percentile: float) -> float:
        key = f"{provider}.{task}"
        latencies = self._latencies.get(key, [])
        if not latencies:
            return 0.0
        sorted_lats = sorted(latencies)
        idx = min(int(len(sorted_lats) * percentile / 100), len(sorted_lats) - 1)
        return sorted_lats[idx]

    def get_success_rate(self, provider: str, task: str) -> float:
        key = f"{provider}.{task}"
        c = self._counters.get(key, {"total": 0, "success": 0})
        if c["total"] == 0:
            return 1.0
        return c["success"] / c["total"]

    def get_all_providers_summary(self) -> dict[str, ProviderStats]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT provider_name FROM provider_metrics"
        ).fetchall()
        return {r[0]: self.get_provider_stats(r[0]) for r in rows}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ──────────────────────────────────────────────
#  BASE PROVIDER
# ──────────────────────────────────────────────


class BaseProvider(ABC):
    """Abstract base class for all AI providers."""

    def __init__(self, name: str, api_key: str = "") -> None:
        self.name = name
        self.api_key = api_key
        self._quota_remaining: Optional[float] = None
        self._quota_reset_at: Optional[str] = None
        self._unavailable_until: Optional[float] = None
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0

    @abstractmethod
    async def call(self, task: str, **kwargs: Any) -> Any:
        ...

    def has_quota(self) -> bool:
        if self._quota_remaining is not None and self._quota_remaining <= 0:
            if self._quota_reset_at:
                reset = datetime.fromisoformat(self._quota_reset_at)
                if datetime.now() < reset:
                    return False
                self._quota_remaining = None
        if self._unavailable_until is not None:
            if time.monotonic() < self._unavailable_until:
                return False
            self._unavailable_until = None
        return True

    def mark_unavailable(self, duration_seconds: float = 3600) -> None:
        self._unavailable_until = time.monotonic() + duration_seconds
        logger.warning("Provider %s marked unavailable for %.0fs", self.name, duration_seconds)

    def get_usage_stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
            "quota_remaining": self._quota_remaining,
            "has_quota": self.has_quota(),
        }

    async def _call_with_retry(
        self,
        task: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs: Any,
    ) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                start = time.monotonic()
                result = await self.call(task=task, **kwargs)
                elapsed = (time.monotonic() - start) * 1000
                self._total_requests += 1
                self._successful_requests += 1
                logger.info(
                    "Provider %s | task=%s | success | %.0fms | attempt=%d/%d",
                    self.name, task, elapsed, attempt, max_retries,
                )
                if isinstance(result, dict) and "latency_ms" not in result:
                    result["latency_ms"] = elapsed
                return result
            except (QuotaExceededError, ProviderNotAvailable):
                raise
            except RateLimitError as exc:
                self._failed_requests += 1
                logger.warning(
                    "Provider %s rate limited (attempt %d/%d): %s",
                    self.name, attempt, max_retries, exc,
                )
                last_exc = exc
                if attempt < max_retries:
                    wait = base_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(wait)
            except TimeoutError as exc:
                self._failed_requests += 1
                logger.warning(
                    "Provider %s timeout (attempt %d/%d): %s",
                    self.name, attempt, max_retries, exc,
                )
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(base_delay * attempt)
            except APIError as exc:
                self._failed_requests += 1
                if 400 <= exc.status_code < 500:
                    logger.error(
                        "Provider %s client error %d (no retry): %s",
                        self.name, exc.status_code, exc,
                    )
                    raise
                logger.warning(
                    "Provider %s server error %d (attempt %d/%d): %s",
                    self.name, exc.status_code, attempt, max_retries, exc,
                )
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
            except Exception as exc:
                self._failed_requests += 1
                logger.error(
                    "Provider %s unexpected error (attempt %d/%d): %s",
                    self.name, attempt, max_retries, exc,
                )
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(base_delay * attempt)

        raise ProviderNotAvailable(
            f"Provider {self.name} failed after {max_retries} retries: {last_exc}"
        )


# ──────────────────────────────────────────────
#  OPENAI-COMPATIBLE LLM CLIENT
# ──────────────────────────────────────────────


class OpenAICompatibleClient(BaseProvider):
    """Base for OpenAI-compatible chat completion providers."""

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        models: list[str],
        default_model: str = "",
        timeout_config: Optional[dict[str, float]] = None,
    ) -> None:
        super().__init__(name)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.models = models
        self.default_model = default_model or (models[0] if models else "")
        self._timeout = httpx.Timeout(
            connect=5.0,
            read=60.0,
            write=30.0,
            pool=10.0,
        )
        if timeout_config:
            self._timeout = httpx.Timeout(**timeout_config)
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "OpenAICompatibleClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owned = self._client is None

        try:
            prompt = kwargs.get("prompt", kwargs.get("text", ""))
            system_prompt = kwargs.get("system_prompt", "")
            model = kwargs.get("model", self.default_model) or self.default_model
            max_tokens = kwargs.get("max_tokens", 2048)
            temperature = kwargs.get("temperature", 0.7)

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            return await self._process_response(response, model)
        finally:
            if owned:
                await client.aclose()

    async def _process_response(self, response: httpx.Response, model: str) -> dict[str, Any]:
        if response.status_code == 429:
            raise RateLimitError(f"{self.name}: rate limit exceeded")

        if response.status_code == 402 or response.status_code == 403:
            body = await response.aread()
            raise QuotaExceededError(f"{self.name}: quota exceeded ({response.status_code})")

        if response.status_code >= 500:
            body = await response.aread()
            raise APIError(
                f"{self.name}: server error {response.status_code}",
                status_code=response.status_code,
                response_body=body.decode(errors="replace"),
            )

        if response.status_code != 200:
            body = await response.aread()
            raise APIError(
                f"{self.name}: HTTP {response.status_code}",
                status_code=response.status_code,
                response_body=body.decode(errors="replace"),
            )

        data = response.json()
        choice = data["choices"][0]
        text = choice["message"]["content"]
        model_used = data.get("model", model)
        tokens_used = data.get("usage", {}).get("total_tokens", 0)

        return {
            "text": text.strip(),
            "model": model_used,
            "tokens_used": tokens_used,
            "provider": self.name,
        }


class OpenRouterClient(OpenAICompatibleClient):
    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__(
            name="openrouter",
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            models=[
                "meta-llama/llama-3.3-70b-instruct",
                "mistralai/mistral-large",
                "deepseek/deepseek-chat",
            ],
            default_model="meta-llama/llama-3.3-70b-instruct",
            timeout_config=timeout_config,
        )


class GroqClient(OpenAICompatibleClient):
    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__(
            name="groq",
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            models=["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
            default_model="llama-3.3-70b-versatile",
            timeout_config=timeout_config,
        )





class CerebrasClient(OpenAICompatibleClient):
    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__(
            name="cerebras",
            api_key=api_key,
            base_url="https://api.cerebras.ai/v1",
            models=["llama3.1-70b"],
            default_model="llama3.1-70b",
            timeout_config=timeout_config,
        )


class DeepSeekClient(OpenAICompatibleClient):
    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__(
            name="deepseek",
            api_key=api_key,
            base_url="https://api.deepseek.com",
            models=["deepseek-chat"],
            default_model="deepseek-chat",
            timeout_config=timeout_config,
        )


# ──────────────────────────────────────────────
#  GEMINI CLIENT (non-OpenAI API)
# ──────────────────────────────────────────────


class GeminiClient(BaseProvider):
    """Google Gemini API client (different schema from OpenAI)."""

    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__("gemini")
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.models = ["gemini-1.5-flash", "gemini-1.5-pro"]
        self.default_model = "gemini-1.5-flash"
        self._timeout = httpx.Timeout(
            connect=5.0, read=60.0, write=30.0, pool=10.0,
            **(timeout_config or {}),
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "GeminiClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owned = self._client is None

        try:
            prompt = kwargs.get("prompt", kwargs.get("text", ""))
            system_prompt = kwargs.get("system_prompt", "")
            model = kwargs.get("model", self.default_model)
            max_tokens = kwargs.get("max_tokens", 2048)
            temperature = kwargs.get("temperature", 0.7)

            contents: list[dict] = [{"parts": [{"text": prompt}]}]
            payload: dict[str, Any] = {
                "contents": contents,
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            }
            if system_prompt:
                payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

            response = await client.post(
                f"{self.base_url}/models/{model}:generateContent",
                params={"key": self.api_key},
                json=payload,
            )

            if response.status_code == 429:
                raise RateLimitError("gemini: rate limit exceeded")
            if response.status_code == 403:
                raise QuotaExceededError("gemini: quota exceeded")
            if response.status_code >= 500:
                body = await response.aread()
                raise APIError(
                    f"gemini: server error {response.status_code}",
                    status_code=response.status_code,
                    response_body=body.decode(errors="replace"),
                )

            data = response.json()
            if "error" in data:
                raise APIError(
                    f"gemini: {data['error'].get('message', str(data))}",
                    status_code=response.status_code,
                )

            candidates = data.get("candidates", [])
            if not candidates:
                raise APIError("gemini: empty response", status_code=200)

            text = ""
            for part in candidates[0].get("content", {}).get("parts", []):
                text += part.get("text", "")

            usage = data.get("usageMetadata", {})
            tokens_used = usage.get("totalTokenCount", 0)

            return {
                "text": text.strip(),
                "model": model,
                "tokens_used": tokens_used,
                "provider": self.name,
            }
        finally:
            if owned:
                await client.aclose()


# ──────────────────────────────────────────────
#  IMAGE PROVIDERS
# ──────────────────────────────────────────────





class ReplicateClient(BaseProvider):
    """Replicate API — async prediction flow (start → poll → download)."""

    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__("replicate")
        self.api_key = api_key
        self.base_url = "https://api.replicate.com/v1"
        self._timeout = httpx.Timeout(
            connect=5.0, read=60.0, write=30.0, pool=10.0,
            **(timeout_config or {}),
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "ReplicateClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", kwargs.get("text", ""))
        model = kwargs.get("model", "black-forest-labs/flux-schnell")
        width = kwargs.get("width", 1080)
        height = kwargs.get("height", 1350)

        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owned = self._client is None

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            prediction = await client.post(
                f"{self.base_url}/models/{model}/predictions",
                headers=headers,
                json={"input": {"prompt": prompt, "width": width, "height": height}},
            )

            if prediction.status_code != 201:
                raise APIError(
                    f"replicate: start failed {prediction.status_code}",
                    status_code=prediction.status_code,
                )

            pred_data = prediction.json()
            prediction_id = pred_data["id"]
            get_url = f"{self.base_url}/predictions/{prediction_id}"

            for _ in range(60):
                await asyncio.sleep(1)
                status_resp = await client.get(get_url, headers=headers)
                status_data = status_resp.json()
                if status_data["status"] == "succeeded":
                    output = status_data.get("output")
                    output_url = output[0] if isinstance(output, list) else output
                    img_resp = await client.get(output_url)
                    return {
                        "image_bytes": img_resp.content,
                        "url": output_url,
                        "format": img_resp.headers.get("content-type", "image/png"),
                        "provider": self.name,
                        "prompt": prompt,
                    }
                if status_data["status"] == "failed":
                    raise APIError(
                        f"replicate: prediction failed: {status_data.get('error', 'unknown')}",
                        status_code=500,
                    )

            raise TimeoutError("replicate: prediction timed out after 60s")

        finally:
            if owned:
                await client.aclose()


class HuggingFaceImageClient(BaseProvider):
    """HuggingFace Inference API for image generation."""

    def __init__(self, api_key: str, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__("huggingface_img")
        self.api_key = api_key
        self.base_url = "https://api-inference.huggingface.co/models"
        self._timeout = httpx.Timeout(
            connect=5.0, read=120.0, write=30.0, pool=10.0,
            **(timeout_config or {}),
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "HuggingFaceImageClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", kwargs.get("text", ""))
        model = kwargs.get("model", "stabilityai/stable-diffusion-xl-base-1.0")
        width = kwargs.get("width", 1080)
        height = kwargs.get("height", 1350)

        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owned = self._client is None

        try:
            response = await client.post(
                f"{self.base_url}/{model}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"inputs": prompt, "parameters": {"width": width, "height": height}},
            )

            if response.status_code == 503:
                raise QuotaExceededError("huggingface_img: model loading / quota exceeded")
            if response.status_code == 429:
                raise RateLimitError("huggingface_img: rate limited")
            if response.status_code != 200:
                raise APIError(
                    f"huggingface_img: HTTP {response.status_code}",
                    status_code=response.status_code,
                )

            return {
                "image_bytes": response.content,
                "format": response.headers.get("content-type", "image/png"),
                "provider": self.name,
                "prompt": prompt,
            }
        finally:
            if owned:
                await client.aclose()


# ──────────────────────────────────────────────
#  TTS PROVIDERS
# ──────────────────────────────────────────────


class EdgeTTSClient(BaseProvider):
    """Microsoft Edge TTS — free, no API key, uses edge-tts library."""

    def __init__(self, timeout_config: Optional[dict[str, float]] = None) -> None:
        super().__init__("edge_tts")
        self.voices = ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"]
        self.default_voice = "ru-RU-DmitryNeural"
        self.output_dir = Path("data/tts")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self) -> "EdgeTTSClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        try:
            import edge_tts
        except ImportError:
            raise ProviderNotAvailable("edge_tts: library not installed (pip install edge-tts)")

        text = kwargs.get("text", kwargs.get("prompt", ""))
        voice = kwargs.get("voice", self.default_voice)
        rate = kwargs.get("rate", "+0%")
        pitch = kwargs.get("pitch", "+0Hz")
        output_format = kwargs.get("format", "mp3")

        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        filename = f"tts_{text_hash}.{output_format}"
        output_path = self.output_dir / filename

        communicate = edge_tts.Communicate(
            text[:5000],
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        await communicate.save(str(output_path))

        return {
            "audio_path": str(output_path),
            "format": output_format,
            "voice": voice,
            "duration_seconds": 0,
            "provider": self.name,
        }


class SileroClient(BaseProvider):
    """Silero TTS — local model via torch."""

    def __init__(self) -> None:
        super().__init__("silero")
        self.voices = ["ru_v3_xenia", "ru_v3_bryan"]
        self.default_voice = "ru_v3_xenia"
        self._model = None
        self.output_dir = Path("data/tts")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self) -> "SileroClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import torch
        except ImportError:
            raise ProviderNotAvailable("silero: torch not installed")
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="ru_v3",
        )
        model.to(device)
        self._model = model
        return model

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        text = kwargs.get("text", kwargs.get("prompt", ""))
        voice = kwargs.get("voice", self.default_voice)
        output_format = kwargs.get("format", "mp3")

        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        filename = f"silero_{text_hash}.{output_format}"
        output_path = self.output_dir / filename

        model = await asyncio.to_thread(self._load_model)
        speaker = voice.replace("ru_v3_", "")

        await asyncio.to_thread(
            model.save_wav,
            text=text[:1000],
            speaker=speaker,
            sample_rate=48000,
            audio_path=str(output_path),
        )

        if output_format == "mp3":
            import subprocess
            mp3_path = output_path.with_suffix(".mp3")
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(output_path), "-codec:a", "libmp3lame", str(mp3_path)],
                capture_output=True,
            )
            output_path = mp3_path

        return {
            "audio_path": str(output_path),
            "format": output_format,
            "voice": voice,
            "provider": self.name,
        }


class BarkClient(BaseProvider):
    """Bark TTS — local model via transformers."""

    def __init__(self) -> None:
        super().__init__("bark")
        self.voices = ["v2/ru_speaker_1", "v2/ru_speaker_2"]
        self.default_voice = "v2/ru_speaker_1"
        self._processor = None
        self._model = None
        self.output_dir = Path("data/tts")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self) -> "BarkClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def _load_model(self) -> tuple[Any, Any]:
        if self._model is not None and self._processor is not None:
            return self._processor, self._model
        try:
            from transformers import AutoProcessor, BarkModel
        except ImportError:
            raise ProviderNotAvailable("bark: transformers not installed")
        processor = AutoProcessor.from_pretrained("suno/bark")
        model = BarkModel.from_pretrained("suno/bark")
        self._processor = processor
        self._model = model
        return processor, model

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        try:
            import scipy.io.wavfile as wavfile
        except ImportError:
            raise ProviderNotAvailable("bark: scipy not installed")

        text = kwargs.get("text", kwargs.get("prompt", ""))
        voice = kwargs.get("voice", self.default_voice)
        output_format = kwargs.get("format", "mp3")

        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        wav_path = self.output_dir / f"bark_{text_hash}.wav"
        output_path = self.output_dir / f"bark_{text_hash}.{output_format}"

        processor, model = await asyncio.to_thread(self._load_model)

        inputs = await asyncio.to_thread(
            processor, text[:500], voice_preset=voice, return_tensors="pt"
        )

        audio_array = await asyncio.to_thread(model.generate, **inputs)
        audio_array = audio_array.cpu().numpy().squeeze()

        await asyncio.to_thread(wavfile.write, str(wav_path), 24000, audio_array)

        if output_format == "mp3":
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", str(output_path)],
                capture_output=True,
            )
            wav_path.unlink(missing_ok=True)
        else:
            output_path = wav_path

        return {
            "audio_path": str(output_path),
            "format": output_format,
            "voice": voice,
            "provider": self.name,
        }


# ──────────────────────────────────────────────
#  ANALYTICS PROVIDERS (LOCAL)
# ──────────────────────────────────────────────


class HFPipelineClient(BaseProvider):
    """Local HuggingFace pipelines for text classification, NER, sentiment."""

    def __init__(self) -> None:
        super().__init__("hf_pipeline")
        self._pipelines: dict[str, Any] = {}
        self._classifier = None
        self._ner = None

    async def __aenter__(self) -> "HFPipelineClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def _get_classifier(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        try:
            from transformers import pipeline
            self._classifier = pipeline(
                "text-classification",
                model="blanchefort/rubert-tiny2-sentiment",
            )
            return self._classifier
        except ImportError:
            raise ProviderNotAvailable("hf_pipeline: transformers not installed")

    def _get_ner(self) -> Any:
        if self._ner is not None:
            return self._ner
        try:
            from transformers import pipeline
            self._ner = pipeline("ner", model="dslim/bert-base-NER")
            return self._ner
        except ImportError:
            raise ProviderNotAvailable("hf_pipeline: transformers not installed")

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        text = kwargs.get("text", kwargs.get("prompt", ""))
        pipeline_type = kwargs.get("pipeline_type", "sentiment")

        if pipeline_type == "sentiment":
            pipe = await asyncio.to_thread(self._get_classifier)
            result = await asyncio.to_thread(pipe, text[:512])
            return {"result": result, "pipeline": pipeline_type, "provider": self.name}

        if pipeline_type == "ner":
            pipe = await asyncio.to_thread(self._get_ner)
            result = await asyncio.to_thread(pipe, text[:512])
            return {"result": result, "pipeline": pipeline_type, "provider": self.name}

        return {"result": [], "pipeline": pipeline_type, "provider": self.name}


class SklearnClient(BaseProvider):
    """Local scikit-learn models for clustering and classification."""

    def __init__(self) -> None:
        super().__init__("local_sklearn")
        self._vectorizer = None
        self._kmeans = None
        self._classifier = None

    async def __aenter__(self) -> "SklearnClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def _ensure_models(self) -> Any:
        if self._vectorizer is not None:
            return
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.cluster import KMeans
            from sklearn.linear_model import LogisticRegression
            self._vectorizer = TfidfVectorizer(max_features=5000, ngram_range=(1, 2))
            self._kmeans = KMeans(n_clusters=10, random_state=42, n_init="auto")
            self._classifier = LogisticRegression(max_iter=1000, random_state=42)
        except ImportError:
            raise ProviderNotAvailable("local_sklearn: scikit-learn not installed")

    async def call(self, task: str, **kwargs: Any) -> dict[str, Any]:
        await asyncio.to_thread(self._ensure_models)
        texts = kwargs.get("texts", [kwargs.get("text", "")])
        operation = kwargs.get("operation", "cluster")

        if not isinstance(texts, list):
            texts = [texts]

        if operation == "cluster":
            vectors = await asyncio.to_thread(
                self._vectorizer.fit_transform, texts
            )
            labels = await asyncio.to_thread(
                self._kmeans.fit_predict, vectors
            )
            return {
                "labels": labels.tolist(),
                "n_clusters": self._kmeans.n_clusters,
                "operation": operation,
                "provider": self.name,
            }

        if operation == "classify":
            vectors = await asyncio.to_thread(
                self._vectorizer.fit_transform, texts
            )
            labels = kwargs.get("labels")
            if labels is not None:
                await asyncio.to_thread(self._classifier.fit, vectors, labels)
            return {
                "operation": operation,
                "provider": self.name,
            }

        return {"operation": operation, "provider": self.name}


# ──────────────────────────────────────────────
#  AI ROUTER
# ──────────────────────────────────────────────


class AIRouter:
    """Intelligent router that selects providers by task with fallbacks and caching."""

    ROUTING_MAP: dict[str, RoutingRule] = {
        "generate_post": RoutingRule(
            providers=["openrouter", "groq", "gemini", "deepseek"],
            default_model="meta-llama/llama-3.3-70b-instruct",
            max_tokens=16000,
            temperature=0.4,
        ),
        "analyze_chat": RoutingRule(
            providers=["groq", "hf_pipeline", "local_sklearn"],
            default_model="mixtral-8x7b-32768",
            max_tokens=4000,
        ),
        "generate_headline": RoutingRule(
            providers=["cerebras", "groq", "openrouter"],
            default_model="llama3.1-70b",
            max_tokens=500,
            temperature=0.8,
        ),
        "classify_topic": RoutingRule(
            providers=["hf_pipeline", "local_sklearn"],
            use_local_first=True,
        ),
        "generate_image": RoutingRule(
            providers=["replicate", "huggingface_img"],
            default_size="1080x1350",
        ),
        "generate_tts": RoutingRule(
            providers=["edge_tts", "silero", "bark"],
            default_voice="ru-RU-DmitryNeural",
        ),
    }

    def __init__(
        self,
        api_keys: Optional[dict[str, str]] = None,
        db_path: str = "data",
        timeout_config: Optional[dict[str, float]] = None,
    ) -> None:
        self.api_keys = api_keys or {}
        self._timeout_config = timeout_config
        self._cache = CacheManager(db_path=f"{db_path}/ai_cache.db")
        self._metrics = MetricsCollector(db_path=f"{db_path}/ai_metrics.db")
        self._providers: dict[str, BaseProvider] = {}
        self._init_providers()

    def _init_providers(self) -> None:
        tc = self._timeout_config
        registry: list[BaseProvider] = [
            OpenRouterClient(self.api_keys.get("openrouter", ""), tc),
            GroqClient(self.api_keys.get("groq", ""), tc),
            CerebrasClient(self.api_keys.get("cerebras", ""), tc),
            GeminiClient(self.api_keys.get("gemini", ""), tc),
            DeepSeekClient(self.api_keys.get("deepseek", ""), tc),
            ReplicateClient(self.api_keys.get("replicate", ""), tc),
            HuggingFaceImageClient(self.api_keys.get("huggingface", ""), tc),
            EdgeTTSClient(tc),
            SileroClient(),
            BarkClient(),
            HFPipelineClient(),
            SklearnClient(),
        ]
        for p in registry:
            self._providers[p.name] = p

    def _get_rule(self, task: str) -> RoutingRule:
        rule = self.ROUTING_MAP.get(task)
        if rule is None:
            rule = RoutingRule(
                providers=["openrouter", "groq"],
                default_model="meta-llama/llama-3.3-70b-instruct",
                max_tokens=2048,
                temperature=0.7,
            )
        return rule

    def _get_provider_chain(self, task: str, custom_chain: Optional[list[str]] = None) -> list[str]:
        if custom_chain:
            return custom_chain
        rule = self._get_rule(task)
        if rule.use_local_first:
            local = [p for p in rule.providers if p in ("hf_pipeline", "local_sklearn", "silero", "bark")]
            remote = [p for p in rule.providers if p not in local]
            return local + remote
        return rule.providers

    def _make_cache_kwargs(self, kwargs: dict) -> dict:
        cache_keys = {"prompt", "text", "model", "task", "voice", "pipeline_type", "operation"}
        return {k: v for k, v in kwargs.items() if k in cache_keys}

    def _get_ttl(self, task: str) -> int:
        long_cache_tasks = {"classify_topic", "analyze_chat"}
        return 604800 if task in long_cache_tasks else 86400

    async def route(self, task: str, **kwargs: Any) -> Any:
        rule = self._get_rule(task)
        provider_chain = self._get_provider_chain(task)
        return await self.call_with_fallback(task, provider_chain, rule, **kwargs)

    async def call_with_fallback(
        self,
        task: str,
        providers_chain: list[str],
        rule: Optional[RoutingRule] = None,
        **kwargs: Any,
    ) -> Any:
        if rule is None:
            rule = self._get_rule(task)

        # Check cache first
        cache_kwargs = self._make_cache_kwargs(kwargs)
        cache_kwargs["task"] = task
        cached = self._cache.get(task, cache_kwargs)
        if cached is not None:
            logger.debug("Route cache HIT: task=%s", task)
            return cached

        last_error: Optional[str] = None
        for provider_name in providers_chain:
            provider = self._providers.get(provider_name)
            if provider is None:
                logger.warning("Unknown provider: %s, skipping", provider_name)
                continue
            if not provider.has_quota():
                logger.warning("Provider %s has no quota, skipping", provider_name)
                continue

            # Fill defaults from rule
            merged = dict(kwargs)
            if rule.default_model and "model" not in merged:
                merged["model"] = rule.default_model
            if rule.max_tokens and "max_tokens" not in merged:
                merged["max_tokens"] = rule.max_tokens
            if rule.temperature and "temperature" not in merged:
                merged["temperature"] = rule.temperature
            if rule.default_size and "width" not in merged:
                parts = rule.default_size.split("x")
                if len(parts) == 2:
                    merged["width"] = int(parts[0])
                    merged["height"] = int(parts[1])
            if rule.default_voice and "voice" not in merged:
                merged["voice"] = rule.default_voice

            try:
                result = await provider._call_with_retry(task=task, **merged)
                elapsed = 0

                # Record metrics
                if isinstance(result, dict):
                    elapsed = result.get("latency_ms", 0)
                    tokens = result.get("tokens_used", 0)
                self._metrics.record_request(
                    provider=provider_name,
                    task=task,
                    success=True,
                    latency_ms=elapsed,
                    tokens_used=tokens if isinstance(tokens, int) else 0,
                )

                # Cache result
                self._cache.set(task, cache_kwargs, result, ttl_seconds=self._get_ttl(task))
                return result

            except (RateLimitError, QuotaExceededError) as exc:
                last_error = str(exc)
                self._metrics.record_request(
                    provider=provider_name, task=task, success=False,
                    latency_ms=0, error_message=str(exc),
                )
                if isinstance(exc, QuotaExceededError):
                    provider.mark_unavailable(3600)
                logger.warning("Provider %s failed (%s), trying next", provider_name, exc)

            except (TimeoutError, APIError, ProviderNotAvailable) as exc:
                last_error = str(exc)
                self._metrics.record_request(
                    provider=provider_name, task=task, success=False,
                    latency_ms=0, error_message=str(exc),
                )
                if isinstance(exc, APIError) and 500 <= exc.status_code < 600:
                    provider.mark_unavailable(300)
                logger.warning("Provider %s failed (%s), trying next", provider_name, exc)

            except Exception as exc:
                last_error = str(exc)
                self._metrics.record_request(
                    provider=provider_name, task=task, success=False,
                    latency_ms=0, error_message=str(exc),
                )
                logger.error("Provider %s unexpected error: %s", provider_name, exc)

        raise ProviderNotAvailable(
            f"All providers exhausted for task '{task}'. Last error: {last_error}"
        )

    def get_provider_stats(self) -> dict[str, ProviderStats]:
        return self._metrics.get_all_providers_summary()

    def get_best_provider(self, task: str) -> str:
        rule = self._get_rule(task)
        best_name = rule.providers[0]
        best_rate = -1.0

        for name in rule.providers:
            rate = self._metrics.get_success_rate(name, task)
            if rate > best_rate:
                best_rate = rate
                best_name = name

        return best_name

    def update_routing(self, task: str, new_priority_list: list[str]) -> None:
        if task in self.ROUTING_MAP:
            old = self.ROUTING_MAP[task]
            self.ROUTING_MAP[task] = RoutingRule(
                providers=new_priority_list,
                default_model=old.default_model,
                max_tokens=old.max_tokens,
                temperature=old.temperature,
                use_local_first=old.use_local_first,
                default_size=old.default_size,
                default_voice=old.default_voice,
            )
            logger.info("Routing updated for '%s': %s", task, new_priority_list)

    async def health_check(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for name, provider in self._providers.items():
            try:
                status = "available" if provider.has_quota() else "quota_exhausted"
                results[name] = {
                    "status": status,
                    "has_api_key": bool(self.api_keys.get(name, "")),
                }
            except Exception as exc:
                results[name] = {"status": "error", "error": str(exc)}
        return results

    def close(self) -> None:
        self._cache.close()
        self._metrics.close()

    async def __aenter__(self) -> "AIRouter":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()
