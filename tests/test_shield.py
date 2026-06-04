"""Tests for anti-ban shield module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from verdikt_auto.anti_ban.shield import AntiBanShield, RateWindow
from verdikt_auto.core.models import GeneratedContent, Post, ProviderName, TaskType


class TestRateWindow:
    def setup_method(self) -> None:
        self.window = RateWindow(max_count=5, window_sec=60.0)

    def test_can_proceed_when_under_limit(self) -> None:
        assert self.window.can_proceed() is True

    def test_cannot_proceed_when_at_limit(self) -> None:
        for _ in range(5):
            self.window.record()
        assert self.window.can_proceed() is False

    def test_count_increases(self) -> None:
        assert self.window.count == 0
        self.window.record()
        assert self.window.count == 1

    def test_count_decreases_after_window(self) -> None:
        self.window._window_sec = -1
        self.window.record()
        assert self.window.count == 0


class TestAntiBanShield:
    def setup_method(self) -> None:
        self.publisher = MagicMock()
        self.publisher.notify_admin = AsyncMock(return_value=True)
        self.shield = AntiBanShield(self.publisher)

    def _make_post(self) -> Post:
        return Post(
            id="test_1",
            topic="Test",
            headline="Test",
            content=GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text="Content",
            ),
        )

    @pytest.mark.asyncio
    async def test_check_before_publish_allows(self) -> None:
        post = self._make_post()
        result = await self.shield.check_before_publish(post)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_before_publish_blocks_when_cooldown(self) -> None:
        self.shield._cooldown_until = 9999999999
        post = self._make_post()
        result = await self.shield.check_before_publish(post)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_before_publish_blocks_when_rate_limited(self) -> None:
        for _ in range(20):
            self.shield._rate_limiter.record()
        post = self._make_post()
        result = await self.shield.check_before_publish(post)
        assert result is False

    @pytest.mark.asyncio
    async def test_record_publish(self) -> None:
        post = self._make_post()
        await self.shield.record_publish(post, 42)
        assert len(self.shield._message_stats) == 1

    @pytest.mark.asyncio
    async def test_shadow_ban_no_subscribers(self) -> None:
        post = self._make_post()
        await self.shield.check_shadow_ban(post, 0)
        assert self.shield._shadow_ban_warnings == 0

    @pytest.mark.asyncio
    async def test_shadow_ban_triggers_warning(self) -> None:
        self.shield.update_subscriber_base(1000)
        post = self._make_post()
        await self.shield.check_shadow_ban(post, 50)
        assert self.shield._shadow_ban_warnings >= 1

    @pytest.mark.asyncio
    async def test_shadow_ban_clears_on_good_reach(self) -> None:
        self.shield.update_subscriber_base(1000)
        self.shield._shadow_ban_warnings = 2
        post = self._make_post()
        await self.shield.check_shadow_ban(post, 500)
        assert self.shield._shadow_ban_warnings == 1

    @pytest.mark.asyncio
    async def test_shadow_ban_three_warnings_triggers_cooldown(self) -> None:
        self.shield.update_subscriber_base(1000)
        post = self._make_post()
        for _ in range(3):
            await self.shield.check_shadow_ban(post, 30)
        assert self.shield._shadow_ban_warnings >= 3
        assert self.shield._is_cooldown()

    def test_get_stats(self) -> None:
        stats = self.shield.get_stats()
        assert "rate_current" in stats
        assert "cooldown_active" in stats
        assert "shadow_ban_warnings" in stats

    def test_update_subscriber_base(self) -> None:
        self.shield.update_subscriber_base(5000)
        assert self.shield._subscriber_base == 5000
