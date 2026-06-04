"""Tests for growth modules."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.growth.auto_commentator import AutoCommentator
from verdikt_auto.growth.gamification import Leaderboard, PointsSystem, WeeklyChallenge
from verdikt_auto.growth.repurpose import RepurposeEngine
from verdikt_auto.core.models import GeneratedContent, Post, ProviderName, TaskType


class TestRepurposeEngine:
    @pytest.mark.asyncio
    async def test_for_shorts(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Короткий текст для Shorts",
        ))
        engine = RepurposeEngine(router)
        post = Post(topic="Test", headline="Test")
        result = await engine.for_shorts(post)
        assert result["platform"] == "shorts"
        assert "Короткий" in result["text"]

    @pytest.mark.asyncio


    @pytest.mark.asyncio
    async def test_for_dzen(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Развёрнутая статья для Дзен",
        ))
        engine = RepurposeEngine(router)
        post = Post(topic="Test", headline="Test")
        result = await engine.for_dzen(post)
        assert result["platform"] == "dzen"

    @pytest.mark.asyncio
    async def test_for_twitter(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Твит 280 символов",
        ))
        engine = RepurposeEngine(router)
        post = Post(topic="Test", headline="Test")
        result = await engine.for_twitter(post)
        assert result["platform"] == "twitter"

    @pytest.mark.asyncio
    async def test_repurpose_all(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Some text",
        ))
        engine = RepurposeEngine(router)
        post = Post(topic="Test", headline="Test")
        results = await engine.repurpose_all(post)
        assert len(results) == 3


class TestPointsSystem:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"users": {}, "history": {}}')
        self.tmp.close()
        self.ps = PointsSystem(path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_award(self) -> None:
        pts = self.ps.award("user_1", "comment")
        assert pts == 10
        assert self.ps.get_balance("user_1") == 10

    def test_leaderboard(self) -> None:
        self.ps.award("a", "comment")
        self.ps.award("b", "share")
        lb = self.ps.get_leaderboard(2)
        assert lb[0]["user_id"] == "b"
        assert lb[1]["points"] == 10

    def test_get_history(self) -> None:
        self.ps.award("user_1", "comment")
        hist = self.ps.get_history("user_1")
        assert len(hist) == 1
        assert hist[0]["action"] == "comment"

    def test_reset_weekly(self) -> None:
        self.ps.award("user_1", "comment")
        self.ps.reset_weekly()
        assert self.ps.get_balance("user_1") == 0


class TestWeeklyChallenge:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"challenges": [], "progress": {}}')
        self.tmp.close()
        self.wc = WeeklyChallenge(path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_start_week(self) -> None:
        self.wc.start_week()
        assert len(self.wc._challenges) == 2

    def test_track_no_challenge(self) -> None:
        result = self.wc.track("user_1", "comment")
        assert result is None

    def test_track_with_challenge(self) -> None:
        self.wc._challenges = [{"title": "Комментатор", "goal": 1, "action": "comment", "reward": 100}]
        result = self.wc.track("user_1", "comment")
        assert result == "Комментатор"

    def test_get_progress(self) -> None:
        self.wc._challenges = [{"title": "Тест", "goal": 5, "action": "share", "reward": 50}]
        self.wc._progress["user_1"]["Тест"] = 3
        prog = self.wc.get_progress("user_1")
        assert prog[0]["current"] == 3


class TestLeaderboard:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"users": {}, "history": {}}')
        self.tmp.close()
        self.ps = PointsSystem(path=self.tmp.name)
        self.lb = Leaderboard(self.ps)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_get_weekly(self) -> None:
        self.ps.award("user_1", "share")
        weekly = self.lb.get_weekly(5)
        assert len(weekly) == 1

    def test_get_all_time_no_file(self) -> None:
        result = self.lb.get_all_time(history_path="data/nonexistent.json", top_n=5)
        assert isinstance(result, list)


class TestAutoCommentator:
    @pytest.mark.asyncio
    async def test_can_comment_initially(self) -> None:
        ac = AutoCommentator(MagicMock())
        assert ac.can_comment() is True

    @pytest.mark.asyncio
    async def test_limit_reached(self) -> None:
        ac = AutoCommentator(MagicMock())
        ac._daily_count = 20
        assert ac.can_comment() is False

    @pytest.mark.asyncio
    async def test_generate_comment(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Интересная мысль, спасибо за анализ!",
        ))
        ac = AutoCommentator(router)
        ac._daily_count = 0
        comment = await ac.generate_comment("Пост текст", "channel_name")
        assert comment is not None
        assert "Интересная" in comment

    @pytest.mark.asyncio
    async def test_generate_when_limit(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="тест",
        ))
        ac = AutoCommentator(router)
        ac._daily_count = 20
        comment = await ac.generate_comment("текст", "chan")
        assert comment is None

    @pytest.mark.asyncio
    async def test_dispatch(self) -> None:
        ac = AutoCommentator(MagicMock())
        result = await ac.dispatch("комментарий", "channel_id")
        assert result is True

    @pytest.mark.asyncio
    async def test_reset_daily(self) -> None:
        ac = AutoCommentator(MagicMock())
        ac._daily_count = 15
        ac.reset_daily()
        assert ac._daily_count == 0

    @pytest.mark.asyncio
    async def test_run_daily_round(self) -> None:
        router = MagicMock()
        router.route = AsyncMock(return_value=GeneratedContent(
            task_type=TaskType.generate_post,
            provider=ProviderName.groq,
            text="Отличный пост!",
        ))
        ac = AutoCommentator(router)
        ac.can_comment = MagicMock(return_value=True)
        ac.dispatch = AsyncMock(return_value=True)
        posts = {"@chan1": "текст1", "@chan2": "текст2"}
        results = await ac.run_daily_round(posts)
        assert len(results) == 2
        assert all(r["success"] for r in results)

    def test_set_targets(self) -> None:
        ac = AutoCommentator(MagicMock())
        ac.set_targets(["@chan1", "@chan2"])
        assert len(ac._target_channels) == 2
