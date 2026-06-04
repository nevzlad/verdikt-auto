"""History manager — reads/writes history.json, tracks announcements, anti-repeats, stats."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CONTENT_PLAN: dict[int, str] = {
    0: "Воскресный дайджест — саммари недели в 5 тезисах",
    1: "Понедельник — новости и аналитика начала недели",
    2: "Вторник — геополитический разбор / эксклюзив",
    3: "Среда — экономика и санкции: цифры, факты, прогнозы",
    4: "Четверг — технологический дайджест / OSINT-расследование",
    5: "Пятница — лёгкий формат: подборка, мем, инсайд",
    6: "Суббота — интервью / длинное чтение / спецпроект",
}

CHANNEL_MANIFEST = (
    "VERDIKT — Telegram-канал о геополитике, экономике и технологиях. "
    "Аналитика без воды, факты без эмоций, вердикты без политики. "
    "Аудитория: 25-45 лет, русскоязычная, интересующаяся мировыми процессами."
)


class HistoryManager:
    """Manages posting history, announcements, content plan, and channel stats."""

    def __init__(self, path: str = "data/history.json") -> None:
        self._path = path
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        path = Path(self._path)
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load history: %s", exc)
        return {"posts": [], "announcements": [], "stats": {}}

    def _save(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get_recent_topics(self, days: int = 14) -> list[dict]:
        cutoff = datetime.now() - timedelta(days=days)
        return [
            p for p in self._data.get("posts", [])
            if datetime.fromisoformat(p.get("published_at", "2000-01-01")) > cutoff
        ]

    def get_announcements(self) -> list[dict]:
        return self._data.get("announcements", [])

    def get_unresolved_announcements(self) -> list[dict]:
        now = datetime.now()
        return [
            a for a in self._data.get("announcements", [])
            if not a.get("resolved", False)
            and datetime.fromisoformat(a.get("scheduled_date", "2000-01-01")) <= now + timedelta(days=3)
        ]

    def get_anti_repeats(self, hours: int = 48) -> list[str]:
        cutoff = datetime.now() - timedelta(hours=hours)
        return [
            p["title"] for p in self._data.get("posts", [])
            if datetime.fromisoformat(p.get("published_at", "2000-01-01")) > cutoff
        ]

    def get_channel_stats(self) -> dict[str, Any]:
        stats = self._data.get("stats", {})
        return {
            "subscribers": stats.get("subscribers", 0),
            "err": stats.get("err", 0.0),
            "posts_last_7d": stats.get("posts_last_7d", 0),
            "avg_views": stats.get("avg_views", 0),
        }

    def add_posted(self, title: str, post_id: str, topic_id: str = "") -> None:
        self._data.setdefault("posts", []).append({
            "title": title,
            "post_id": post_id,
            "topic_id": topic_id,
            "published_at": datetime.now().isoformat(),
        })
        self._prune_posts()
        self._save()

    def add_announcement(self, title: str, scheduled_date: str, topic_id: str = "") -> None:
        self._data.setdefault("announcements", []).append({
            "title": title,
            "topic_id": topic_id,
            "scheduled_date": scheduled_date,
            "resolved": False,
            "created_at": datetime.now().isoformat(),
        })
        self._save()

    def resolve_announcement(self, title: str) -> None:
        for a in self._data.get("announcements", []):
            if a["title"] == title and not a["resolved"]:
                a["resolved"] = True
                a["resolved_at"] = datetime.now().isoformat()
                self._save()
                break

    def count_in_last_7_days(self, keywords: list[str]) -> int:
        cutoff = datetime.now() - timedelta(days=7)
        count = 0
        for p in self._data.get("posts", []):
            pub = datetime.fromisoformat(p.get("published_at", "2000-01-01"))
            if pub > cutoff:
                post_kw = set(p.get("keywords", []))
                topic_kw = set(keywords)
                if post_kw & topic_kw:
                    count += 1
        return count

    def get_content_plan(self, weekday: Optional[int] = None) -> str:
        wd = weekday if weekday is not None else datetime.now().weekday()
        return CONTENT_PLAN.get(wd, "Стандартный формат")

    def update_stats(self, subscribers: int, err: float, posts_last_7d: int, avg_views: int) -> None:
        self._data["stats"] = {
            "subscribers": subscribers,
            "err": err,
            "posts_last_7d": posts_last_7d,
            "avg_views": avg_views,
        }
        self._save()

    def _prune_posts(self, max_days: int = 30) -> None:
        cutoff = datetime.now() - timedelta(days=max_days)
        self._data["posts"] = [
            p for p in self._data.get("posts", [])
            if datetime.fromisoformat(p.get("published_at", "2000-01-01")) > cutoff
        ]
