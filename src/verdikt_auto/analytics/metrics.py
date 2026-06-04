"""Analytics — channel metrics, post performance tracking, SQLite storage."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from verdikt_auto.core.models import ChannelStats, Post

logger = logging.getLogger(__name__)


class PostPerformanceTracker:
    """Tracks post views/reactions at 1h / 4h / 24h intervals."""

    def __init__(self, db_path: str = "data/metrics.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS post_performance (
                post_id TEXT,
                interval TEXT,
                views INTEGER DEFAULT 0,
                reactions INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                forwards INTEGER DEFAULT 0,
                collected_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (post_id, interval)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                posts INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                reactions INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                new_subscribers INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

    def record(self, post_id: str, interval: str, views: int = 0, reactions: int = 0,
               comments: int = 0, forwards: int = 0) -> None:
        self._conn.execute("""
            INSERT INTO post_performance (post_id, interval, views, reactions, comments, forwards)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id, interval) DO UPDATE SET
                views = excluded.views,
                reactions = excluded.reactions,
                comments = excluded.comments,
                forwards = excluded.forwards,
                collected_at = datetime('now')
        """, (post_id, interval, views, reactions, comments, forwards))
        self._conn.commit()

    def get(self, post_id: str) -> dict[str, dict[str, int]]:
        rows = self._conn.execute(
            "SELECT interval, views, reactions, comments, forwards FROM post_performance WHERE post_id = ?",
            (post_id,),
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for interval, views, reactions, comments, forwards in rows:
            result[interval] = {"views": views, "reactions": reactions, "comments": comments, "forwards": forwards}
        return result

    def get_all_recent(self, hours: int = 24) -> list[dict[str, Any]]:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute("""
            SELECT post_id, interval, views, reactions, comments, forwards
            FROM post_performance
            WHERE collected_at >= ?
            ORDER BY collected_at DESC
        """, (cutoff,)).fetchall()
        return [
            {"post_id": r[0], "interval": r[1], "views": r[2], "reactions": r[3],
             "comments": r[4], "forwards": r[5]}
            for r in rows
        ]

    def record_daily(self, date: str, posts: int = 0, views: int = 0, reactions: int = 0,
                     comments: int = 0, new_subscribers: int = 0) -> None:
        self._conn.execute("""
            INSERT INTO daily_stats (date, posts, views, reactions, comments, new_subscribers)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                posts = excluded.posts,
                views = excluded.views,
                reactions = excluded.reactions,
                comments = excluded.comments,
                new_subscribers = excluded.new_subscribers
        """, (date, posts, views, reactions, comments, new_subscribers))
        self._conn.commit()

    def get_daily(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._conn.execute("""
            SELECT date, posts, views, reactions, comments, new_subscribers
            FROM daily_stats WHERE date >= ? ORDER BY date
        """, (cutoff,)).fetchall()
        return [
            {"date": r[0], "posts": r[1], "views": r[2], "reactions": r[3],
             "comments": r[4], "new_subscribers": r[5]}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


class MetricsCollector:
    """Collects channel and post metrics from Telegram Bot API / TGStat."""

    def __init__(self, db_path: str = "data/metrics.db") -> None:
        self.tracker = PostPerformanceTracker(db_path)

    async def collect_post_metrics(self, post: Post, message_id: int, bot_token: str, channel_id: str) -> None:
        try:
            from telegram import Bot
            bot = Bot(token=bot_token)
            msg = await bot.get_chat(chat_id=channel_id, message_id=message_id)
            views = getattr(msg, "views", 0)
            reactions = len(getattr(msg, "reactions", []) or [])
            self.tracker.record(
                post_id=post.id or str(message_id),
                interval="1h",
                views=views,
                reactions=reactions,
            )
            logger.info("Collected metrics for post %s: %d views", post.id[:8], views)
        except ImportError:
            logger.warning("python-telegram-bot not installed")
        except Exception as exc:
            logger.error("Failed to collect metrics: %s", exc)

    async def collect_daily(self, bot_token: str, channel_id: str) -> dict[str, int]:
        try:
            from telegram import Bot
            bot = Bot(token=bot_token)
            chat = await bot.get_chat(chat_id=channel_id)
            members = getattr(chat, "full_member_count", 0) or getattr(chat, "members_count", 0)
            date = datetime.now().strftime("%Y-%m-%d")
            self.tracker.record_daily(date=date, new_subscribers=members)
            return {"subscribers": members, "date": date}
        except ImportError:
            return {}
        except Exception as exc:
            logger.error("Daily collection failed: %s", exc)
            return {}

    def get_channel_stats(self) -> ChannelStats:
        daily = self.tracker.get_daily(7)
        total_views = sum(d["views"] for d in daily)
        total_posts = sum(d["posts"] for d in daily)
        recent = self.tracker.get_all_recent(24)
        top_posts = sorted(
            {r["post_id"] for r in recent},
            key=lambda pid: max(
                (x["views"] for x in recent if x["post_id"] == pid),
                default=0,
            ),
            reverse=True,
        )[:10]
        return ChannelStats(
            posts_count=total_posts,
            views_last_7d=total_views,
            top_posts=list(top_posts),
            avg_engagement=round(
                sum(d["reactions"] + d["comments"] for d in daily) / max(total_posts, 1), 2
            ),
        )

    def close(self) -> None:
        self.tracker.close()
