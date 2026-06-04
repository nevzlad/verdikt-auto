"""Scanners — collect topics from YouTube, Telegram, RSS, Trends."""

from verdikt_auto.scanner.models import Topic, TopicCategory, SourceConfig
from verdikt_auto.scanner.base_scanner import BaseScanner
from verdikt_auto.scanner.youtube_scanner import YouTubeScanner
from verdikt_auto.scanner.telegram_scanner import TelegramScanner
from verdikt_auto.scanner.rss_scanner import RSSScanner
from verdikt_auto.scanner.trends_scanner import TrendsScanner
from verdikt_auto.scanner.topic_aggregator import TopicAggregator
from verdikt_auto.scanner.topics_db import TopicsDB
from verdikt_auto.scanner.sources_manager import SourcesManager

__all__ = [
    "BaseScanner",
    "YouTubeScanner",
    "TelegramScanner",
    "RSSScanner",
    "TrendsScanner",
    "Topic",
    "TopicCategory",
    "SourceConfig",
    "TopicAggregator",
    "TopicsDB",
    "SourcesManager",
]
