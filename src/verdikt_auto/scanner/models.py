"""Data models for scanner module."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class TopicCategory(str, Enum):
    SVO = "svo"
    MIDEAST = "mideast"
    USA = "usa"
    ECONOMY = "economy"
    TECH = "tech"
    GENERAL = "general"

    @classmethod
    def from_keywords(cls, keywords: list[str]) -> "TopicCategory":
        text = " ".join(keywords).lower()
        if any(kw in text for kw in ("сво", "украин", "всу", "донбас", "спецоперац")):
            return cls.SVO
        if any(kw in text for kw in ("израиль", "палестин", "газа", "иран", "ближн")):
            return cls.MIDEAST
        if any(kw in text for kw in ("сша", "америк", "трамп", "байден", "вашингтон")):
            return cls.USA
        if any(kw in text for kw in ("экономик", "рынок", "биржа", "нефт", "газ", "доллар", "рубл")):
            return cls.ECONOMY
        if any(kw in text for kw in ("ai", "ии", "технологи", "цифров", "нейросет", "чатгпт")):
            return cls.TECH
        return cls.GENERAL


@dataclass
class Topic:
    id: str
    title: str
    source: str
    source_name: str
    url: str
    published_at: datetime
    views: int
    engagement: float
    keywords: list[str]
    category: TopicCategory
    raw_data: dict = field(default_factory=dict)
    viral_score: float = 0.0
    relevance_score: float = 0.0

    @classmethod
    def create_id(cls, title: str, source: str) -> str:
        raw = f"{title.strip().lower()}:{source}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @classmethod
    def from_raw(
        cls,
        title: str,
        source: str,
        source_name: str,
        url: str,
        published_at: Optional[datetime],
        views: int = 0,
        likes: int = 0,
        comments: int = 0,
        shares: int = 0,
        keywords: Optional[list[str]] = None,
        raw_data: Optional[dict] = None,
    ) -> "Topic":
        kws = keywords or []
        category = TopicCategory.from_keywords(kws + [title])
        engagement = ((likes + comments + shares) / max(views, 1)) * 100.0
        tid = cls.create_id(title, source)
        published = published_at or datetime.now()

        return cls(
            id=tid,
            title=title,
            source=source,
            source_name=source_name,
            url=url,
            published_at=published,
            views=views,
            engagement=round(engagement, 2),
            keywords=kws,
            category=category,
            raw_data=raw_data or {},
        )


class SourceConfig:
    """Configuration for a single source."""

    def __init__(self, source_type: str, name: str, identifier: str, keywords: Optional[list[str]] = None) -> None:
        self.source_type = source_type
        self.name = name
        self.identifier = identifier
        self.keywords = keywords or []

    def to_dict(self) -> dict:
        return {
            "type": self.source_type,
            "name": self.name,
            "identifier": self.identifier,
            "keywords": self.keywords,
        }
