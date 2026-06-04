"""Telegram SEO optimizer — optimizes posts for Telegram search discovery."""

import hashlib
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from verdikt_auto.core.models import Post

logger = logging.getLogger(__name__)


class KeywordDB:
    """SQLite-backed keyword database for SEO analysis."""

    def __init__(self, db_path: str = "data/seo_keywords.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS keywords (
                keyword TEXT PRIMARY KEY,
                frequency INTEGER DEFAULT 1,
                last_seen TEXT DEFAULT (datetime('now')),
                avg_position REAL DEFAULT 0.0,
                impression_count INTEGER DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS keyword_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT,
                post_id TEXT,
                position INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def record_keyword(self, keyword: str, post_id: str = "", position: int = 0) -> None:
        kw = keyword.lower().strip()
        if not kw:
            return
        self._conn.execute(
            """INSERT INTO keywords (keyword, frequency, last_seen)
               VALUES (?, 1, datetime('now'))
               ON CONFLICT(keyword) DO UPDATE SET
                   frequency = frequency + 1,
                   last_seen = datetime('now')""",
            (kw,),
        )
        if post_id:
            self._conn.execute(
                "INSERT INTO keyword_posts (keyword, post_id, position) VALUES (?, ?, ?)",
                (kw, post_id, position),
            )
        self._conn.commit()

    def get_top_keywords(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT keyword, frequency, last_seen FROM keywords ORDER BY frequency DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"keyword": r[0], "frequency": r[1], "last_seen": r[2]}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


class TelegramSEOOptimizer:
    """Optimizes post content for Telegram search and indexing."""

    PREVIEW_LENGTH = 50

    def __init__(self, db_path: str = "data/seo_keywords.db") -> None:
        self.kw_db = KeywordDB(db_path)
        self._telethon_client: Optional[object] = None

    async def optimize_post(self, post: Post) -> Post:
        text = post.content.text if post.content else post.headline
        if not text:
            return post

        first_50 = text[: self.PREVIEW_LENGTH]
        first_word = first_50.split()[0] if first_50.split() else ""

        keywords = [first_word] + post.tags[:3] if post.tags else [first_word]

        if first_word.lower() not in first_50.lower():
            text = f"{first_word}: {text}"

        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in text.lower():
                text = f"{kw}: {text}"

        if post.content:
            from verdikt_auto.core.models import GeneratedContent
            post.content = GeneratedContent(
                task_type=post.content.task_type,
                provider=post.content.provider,
                text=text,
                model=post.content.model,
                tokens_used=post.content.tokens_used,
            )

        hashtag_mix = self._generate_hashtag_mix(text, keywords)
        post.tags = hashtag_mix

        for kw in keywords:
            self.kw_db.record_keyword(kw, post.id)

        return post

    def _generate_hashtag_mix(self, text: str, keywords: list[str], max_tags: int = 7) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        def _add(tag: str) -> None:
            clean = re.sub(r"[^\w]", "", tag).lower()
            if clean and clean not in seen:
                seen.add(clean)
                tags.append(clean)

        _add("вердикт")

        for kw in keywords:
            _add(kw)

        words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{4,}", text)
        for word in words:
            if len(tags) >= max_tags:
                break
            _add(word)

        return tags[:max_tags]

    async def check_indexing(self, post: Post) -> bool:
        if self._telethon_client is None:
            try:
                from telethon import TelegramClient
                self._telethon_client = object()
            except ImportError:
                logger.warning("Telethon not installed — indexing check disabled")
                return True

        if not hasattr(self._telethon_client, "search_messages"):
            return True

        try:
            text_to_search = post.headline[:30]
            async with self._telethon_client:
                messages = await self._telethon_client.search_messages(
                    "me", text_to_search, limit=1
                )
                return len(messages) > 0
        except Exception as exc:
            logger.warning("Indexing check failed: %s", exc)
            return True

    async def close(self) -> None:
        self.kw_db.close()
