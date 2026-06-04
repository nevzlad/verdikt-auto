"""Gamification — points system, weekly challenges, leaderboard."""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PointsSystem:
    """Tracks user points for engagement actions."""

    RULES: dict[str, int] = {
        "comment": 10,
        "share": 20,
        "reaction": 5,
        "visit": 1,
        "referral": 50,
        "daily_bonus": 15,
    }

    def __init__(self, path: str = "data/points.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._users: dict[str, int] = {}
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._users = data.get("users", {})
                raw = data.get("history", {})
                self._history = defaultdict(list, {k: v for k, v in raw.items()})
            except (json.JSONDecodeError, ValueError):
                pass

    def _save(self) -> None:
        data = {
            "users": self._users,
            "history": dict(self._history),
        }
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def award(self, user_id: str, action: str) -> int:
        points = self.RULES.get(action, 1)
        self._users[user_id] = self._users.get(user_id, 0) + points
        self._history[user_id].append({
            "action": action,
            "points": points,
            "timestamp": datetime.now().isoformat(),
        })
        self._save()
        logger.debug("Awarded %d points to %s for %s", points, user_id[:8], action)
        return points

    def get_balance(self, user_id: str) -> int:
        return self._users.get(user_id, 0)

    def get_leaderboard(self, top_n: int = 10) -> list[dict[str, Any]]:
        ranked = sorted(self._users.items(), key=lambda x: x[1], reverse=True)
        return [
            {"user_id": uid[:8], "points": pts}
            for uid, pts in ranked[:top_n]
        ]

    def get_history(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._history.get(user_id, [])[-limit:]

    def reset_weekly(self) -> None:
        self._users.clear()
        self._history.clear()
        self._save()
        logger.info("Weekly points reset")


class WeeklyChallenge:
    """Creates and tracks weekly engagement challenges."""

    TEMPLATES: list[dict[str, Any]] = [
        {"title": "Комментатор недели", "goal": 10, "action": "comment", "reward": 100},
        {"title": "Ретранслятор", "goal": 5, "action": "share", "reward": 150},
        {"title": "Активный читатель", "goal": 20, "action": "reaction", "reward": 80},
        {"title": "Приведи друга", "goal": 3, "action": "referral", "reward": 200},
    ]

    def __init__(self, path: str = "data/challenges.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._challenges: list[dict[str, Any]] = []
        self._progress: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._challenges = data.get("challenges", [])
                raw = data.get("progress", {})
                self._progress = defaultdict(
                    lambda: defaultdict(int),
                    {k: defaultdict(int, v) for k, v in raw.items()},
                )
            except (json.JSONDecodeError, ValueError):
                pass

    def _save(self) -> None:
        data = {
            "challenges": self._challenges,
            "progress": dict(self._progress),
        }
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def start_week(self) -> None:
        import random
        self._challenges = random.sample(self.TEMPLATES, min(2, len(self.TEMPLATES)))
        self._progress.clear()
        self._save()
        logger.info("Weekly challenges started: %s", [c["title"] for c in self._challenges])

    def track(self, user_id: str, action: str) -> Optional[str]:
        for challenge in self._challenges:
            if challenge["action"] == action:
                self._progress[user_id][challenge["title"]] += 1
                if self._progress[user_id][challenge["title"]] >= challenge["goal"]:
                    self._save()
                    return challenge["title"]
        self._save()
        return None

    def get_progress(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {"title": c["title"], "goal": c["goal"], "current": self._progress[user_id].get(c["title"], 0),
             "action": c["action"], "reward": c["reward"]}
            for c in self._challenges
        ]


class Leaderboard:
    """Weekly + all-time leaderboard UI data."""

    def __init__(self, points_system: PointsSystem) -> None:
        self.points = points_system

    def get_weekly(self, top_n: int = 10) -> list[dict[str, Any]]:
        return self.points.get_leaderboard(top_n)

    def get_all_time(self, history_path: str = "data/leaderboard_all.json", top_n: int = 10) -> list[dict[str, Any]]:
        path = Path(history_path)
        if not path.exists():
            return self.points.get_leaderboard(top_n)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ranked = sorted(data.items(), key=lambda x: x[1], reverse=True)
            return [
                {"user_id": uid[:8], "points": pts, "badge": self._badge(i)}
                for i, (uid, pts) in enumerate(ranked[:top_n])
            ]
        except (json.JSONDecodeError, ValueError):
            return self.points.get_leaderboard(top_n)

    def _badge(self, rank: int) -> str:
        if rank == 0:
            return "🥇"
        if rank == 1:
            return "🥈"
        if rank == 2:
            return "🥉"
        return f"#{rank + 1}"
