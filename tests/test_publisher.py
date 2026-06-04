"""Tests for publisher module: TelegramPublisher, PostScheduler, BroadcastManager."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.core.models import GeneratedContent, Post, ProviderName, TaskType
from verdikt_auto.publisher.telegram_publisher import (
    BOT_TOKEN_PLACEHOLDER,
    BroadcastManager,
    PostScheduler,
    TelegramPublisher,
)


class FakeMessage:
    def __init__(self, message_id: int = 42) -> None:
        self.message_id = message_id


class TestTelegramPublisher:
    def setup_method(self) -> None:
        settings = MagicMock()
        settings.telegram.telegram_bot_token = "fake:token"
        settings.telegram.telegram_channel_id = "@test_channel"
        settings.telegram.admin_chat_id = "12345"

        with patch("verdikt_auto.publisher.telegram_publisher.TelegramPublisher._get_bot") as mock_get_bot:
            self.bot = AsyncMock()
            self.bot.send_message = AsyncMock(return_value=FakeMessage(42))
            self.bot.send_photo = AsyncMock(return_value=FakeMessage(43))
            self.bot.send_voice = AsyncMock(return_value=FakeMessage(44))
            mock_get_bot.return_value = self.bot
            self.publisher = TelegramPublisher(settings)
            self.publisher._get_bot = AsyncMock(return_value=self.bot)

    def _make_post(self) -> Post:
        return Post(
            id="test_123",
            topic="Test",
            headline="Test Headline",
            content=GeneratedContent(
                task_type=TaskType.generate_post,
                provider=ProviderName.groq,
                text="Test content for the post body that should be published",
            ),
            tags=["news", "tech"],
        )

    @pytest.mark.asyncio
    async def test_publish_post_returns_message_id(self) -> None:
        post = self._make_post()
        msg_id = await self.publisher.publish_post(post)
        assert msg_id == 42

    @pytest.mark.asyncio
    async def test_publish_with_photo(self) -> None:
        post = self._make_post()
        with patch("builtins.open", new_callable=MagicMock) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"fake_image"
            msg_id = await self.publisher.publish_with_photo(post, "fake.jpg")
            assert msg_id == 43

    @pytest.mark.asyncio
    async def test_publish_with_voice(self) -> None:
        post = self._make_post()
        with patch("builtins.open", new_callable=MagicMock) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"fake_audio"
            msg_id = await self.publisher.publish_with_voice(post, "fake.ogg")
            assert msg_id == 44

    @pytest.mark.asyncio
    async def test_notify_admin(self) -> None:
        result = await self.publisher.notify_admin("Test message")
        assert result is True

    @pytest.mark.asyncio
    async def test_publish_retry_on_failure(self) -> None:
        self.bot.send_message = AsyncMock(side_effect=[Exception("fail"), Exception("fail"), FakeMessage(45)])
        post = self._make_post()
        msg_id = await self.publisher.publish_post(post)
        assert msg_id == 45

    @pytest.mark.asyncio
    async def test_publish_all_fail(self) -> None:
        self.bot.send_message = AsyncMock(side_effect=Exception("always fail"))
        post = self._make_post()
        msg_id = await self.publisher.publish_post(post)
        assert msg_id is None


class TestPostScheduler:
    def setup_method(self) -> None:
        self.publisher = AsyncMock(spec=TelegramPublisher)
        self.publisher.publish_post = AsyncMock(return_value=42)
        self.scheduler = PostScheduler(self.publisher, schedule_hours=[12, 18], posts_per_day=2)

    @pytest.mark.asyncio
    async def test_enqueue_and_process(self) -> None:
        post = Post(topic="Test", headline="Test")
        processed = []

        async def process(post: Post) -> None:
            processed.append(post)

        self.scheduler.set_post_fn(process)
        await self.scheduler.enqueue(post)
        assert self.scheduler._queue.qsize() == 1

    def test_next_hour(self) -> None:
        from datetime import datetime, timedelta
        now = datetime.now().replace(hour=10, minute=30)
        next_h = self.scheduler._next_hour(now)
        assert next_h is not None
        assert next_h.hour == 12

    def test_next_hour_wraps_to_tomorrow(self) -> None:
        from datetime import datetime
        now = datetime.now().replace(hour=23, minute=30)
        next_h = self.scheduler._next_hour(now)
        assert next_h is not None
        assert next_h.hour == 12

    @pytest.mark.asyncio
    async def test_adjust_schedule(self) -> None:
        old_len = len(self.scheduler._schedule_hours)
        await self.scheduler.adjust_schedule({"evening_engagement": 0.85})
        assert len(self.scheduler._schedule_hours) >= old_len

    def test_stop(self) -> None:
        self.scheduler.stop()
        assert self.scheduler._running is False


class TestBroadcastManager:
    def setup_method(self) -> None:
        self.publisher = AsyncMock(spec=TelegramPublisher)
        self.publisher.notify_admin = AsyncMock(return_value=True)
        self.bm = BroadcastManager(self.publisher)

    @pytest.mark.asyncio
    async def test_send_weekly_digest(self) -> None:
        posts = [
            Post(topic="T1", headline="Headline 1", tags=["a"]),
            Post(topic="T2", headline="Headline 2", tags=["b"]),
        ]
        result = await self.bm.send_weekly_digest(posts)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_urgent_verdict_fails_without_token(self) -> None:
        post = Post(topic="Urgent", headline="Breaking news")
        result = await self.bm.send_urgent_verdict(post)
        assert result is None

    @pytest.mark.asyncio
    async def test_welcome_message(self) -> None:
        with patch("telegram.Bot.send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = FakeMessage(1)
            result = await self.bm.send_welcome("@test_channel")
            assert result is True
