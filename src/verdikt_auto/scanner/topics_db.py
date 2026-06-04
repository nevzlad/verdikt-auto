"""TopicsDB — SQLite storage for scanned topics."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from verdikt_auto.scanner.models import Topic, TopicCategory

logger = logging.getLogger(__name__)


class TopicsDB:
    """SQLite-backed topic store with deduplication and query methods."""

    def __init__(self, db_path: str = "data/topics.db") -> None:
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
            """CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                source_name TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT NOT NULL,
                views INTEGER DEFAULT 0,
                engagement REAL DEFAULT 0.0,
                keywords_json TEXT DEFAULT '[]',
                category TEXT DEFAULT 'general',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                viral_score REAL DEFAULT 0.0,
                relevance_score REAL DEFAULT 0.0
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_category_created ON topics(category, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viral_score ON topics(viral_score DESC)"
        )
        conn.commit()

    def add_topic(self, topic: Topic) -> bool:
        """Insert or ignore (by id). Returns True if inserted."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO topics
                   (id, title, source, source_name, url, published_at,
                    views, engagement, keywords_json, category, viral_score, relevance_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    topic.id,
                    topic.title,
                    topic.source,
                    topic.source_name,
                    topic.url,
                    topic.published_at.isoformat(),
                    topic.views,
                    topic.engagement,
                    json.dumps(topic.keywords, ensure_ascii=False),
                    topic.category.value,
                    topic.viral_score,
                    topic.relevance_score,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Failed to insert topic %s: %s", topic.id, exc)
            return False

    def add_topics(self, topics: list[Topic]) -> int:
        """Batch insert. Returns count of new topics."""
        count = 0
        for t in topics:
            if self.add_topic(t):
                count += 1
        logger.info("Inserted %d/%d new topics", count, len(topics))
        return count

    def get_recent(self, limit: int = 50) -> list[Topic]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM topics ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_topic(r) for r in rows]

    def get_by_category(self, category: TopicCategory, limit: int = 20) -> list[Topic]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM topics WHERE category = ? ORDER BY viral_score DESC LIMIT ?",
            (category.value, limit),
        ).fetchall()
        return [self._row_to_topic(r) for r in rows]

    def get_top_viral(self, limit: int = 20) -> list[Topic]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM topics ORDER BY viral_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_topic(r) for r in rows]

    def count(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM topics").fetchone()
        return row["cnt"] if row else 0

    def deduplicate(self) -> int:
        """Remove duplicate rows keeping the one with highest viral_score."""
        conn = self._get_conn()
        result = conn.execute(
            """DELETE FROM topics WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY title COLLATE NOCASE
                        ORDER BY viral_score DESC
                    ) as rn
                    FROM topics
                ) WHERE rn > 1
            )"""
        )
        conn.commit()
        deleted = result.rowcount
        if deleted:
            logger.info("Deduplicated %d topics", deleted)
        return deleted

    def search(self, query: str, limit: int = 20) -> list[Topic]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM topics WHERE title LIKE ? ORDER BY viral_score DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [self._row_to_topic(r) for r in rows]

    def _row_to_topic(self, row: sqlite3.Row) -> Topic:
        return Topic(
            id=row["id"],
            title=row["title"],
            source=row["source"],
            source_name=row["source_name"],
            url=row["url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            views=row["views"],
            engagement=row["engagement"],
            keywords=json.loads(row["keywords_json"]),
            category=TopicCategory(row["category"]),
            viral_score=row["viral_score"],
            relevance_score=row["relevance_score"],
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
