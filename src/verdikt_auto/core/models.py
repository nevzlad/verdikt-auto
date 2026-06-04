"""Pydantic models for domain entities."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    summary = "summary"
    rewrite = "rewrite"
    creative = "creative"
    headline = "headline"
    seo = "seo"
    image_prompt = "image_prompt"
    generate_post = "generate_post"
    analyze_chat = "analyze_chat"
    classify_topic = "classify_topic"
    generate_image = "generate_image"
    generate_tts = "generate_tts"


class ProviderName(str, Enum):
    openrouter = "openrouter"
    groq = "groq"
    cerebras = "cerebras"
    gemini = "gemini"
    deepseek = "deepseek"
    replicate = "replicate"
    huggingface_img = "huggingface_img"
    edge_tts = "edge_tts"
    silero = "silero"
    bark = "bark"
    hf_pipeline = "hf_pipeline"
    local_sklearn = "local_sklearn"


class SourceType(str, Enum):
    youtube = "youtube"
    telegram = "telegram"
    rss = "rss"
    trends = "trends"
    manual = "manual"


class RawPost(BaseModel):
    source_id: str
    source_type: SourceType
    title: str
    content: str
    url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    metadata: dict[str, str] = Field(default_factory=dict)
    raw_text: str = ""


class RankedTopic(BaseModel):
    topic_id: str
    title: str
    score: float
    normalized_views: float = 0.0
    normalized_engagement: float = 0.0
    relevance_score: float = 0.0
    continuity_bonus: float = 0.0
    novelty_score: float = 0.0
    viral_potential: float = 0.0
    seo_score: float = 0.0
    explanation: str = ""


class GeneratedContent(BaseModel):
    task_type: TaskType
    provider: ProviderName
    text: str
    model: str = ""
    tokens_used: int = 0
    generation_time: float = 0.0


class HeadlineType(str, Enum):
    paradox = "paradox"
    question = "question"
    shock_fact = "shock_fact"


class HeadlineVariant(BaseModel):
    text: str
    type: HeadlineType
    score: float = 0.0


class PostCategory(str, Enum):
    SVO = "svo"
    MIDEAST = "mideast"
    USA = "usa"
    ECONOMY = "economy"
    TECH = "tech"
    GENERAL = "general"

    @classmethod
    def from_keywords(cls, keywords: list[str]) -> "PostCategory":
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


class Post(BaseModel):
    id: str = ""
    topic: str
    content: GeneratedContent = Field(default_factory=lambda: GeneratedContent(task_type=TaskType.summary, provider=ProviderName.groq, text=""))
    headline: str = ""
    headlines: list[HeadlineVariant] = Field(default_factory=list)
    image_url: Optional[str] = None
    image_prompt: str = ""
    tts_path: Optional[str] = None
    tts_text: str = ""
    tags: list[str] = Field(default_factory=list)
    category: PostCategory = PostCategory.GENERAL
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    status: str = "draft"


class ChannelStats(BaseModel):
    subscribers_count: int = 0
    posts_count: int = 0
    views_last_7d: int = 0
    avg_engagement: float = 0.0
    top_posts: list[str] = Field(default_factory=list)


class RoutingRule(BaseModel):
    providers: list[str]
    default_model: str = ""
    max_tokens: int = 2048
    temperature: float = 0.7
    use_local_first: bool = False
    default_size: str = ""
    default_voice: str = ""


class ProviderStats(BaseModel):
    name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    quota_remaining: Optional[float] = None
    last_error: Optional[str] = None
    is_available: bool = True
    unavailable_until: Optional[datetime] = None


class CacheEntry(BaseModel):
    cache_key: str
    response: Any
    task: str
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: datetime
