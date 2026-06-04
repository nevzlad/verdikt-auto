"""Content validator — ensures post quality standards."""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from verdikt_auto.core.models import Post

logger = logging.getLogger(__name__)


class ValidationResult:
    def __init__(self) -> None:
        self.is_valid: bool = True
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def add_error(self, msg: str) -> None:
        self.is_valid = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class ContentValidator:
    """Validates generated posts against quality criteria."""

    MIN_LENGTH = 400
    MAX_LENGTH = 600
    MIN_HEADLINE_LENGTH = 10
    MAX_HEADLINE_LENGTH = 120
    MAX_POSTS_PER_PUBLICATION = 7
    REPUBLISH_COOLDOWN_HOURS = 48

    RUSSIAN_DOMAINS: set[str] = {
        "ria.ru", "tass.ru", "rbc.ru", "kommersant.ru", "lenta.ru",
        "gazeta.ru", "iz.ru", "kp.ru", "mk.ru", "aif.ru", "rg.ru",
        "1tv.ru", "vesti.ru", "rt.com", "russian.rt.com",
    }

    CTA_PATTERNS: list[re.Pattern] = [
        re.compile(r"подписывайся|подпишись", re.IGNORECASE),
        re.compile(r"ставь|поставь\s+лайк", re.IGNORECASE),
        re.compile(r"делись|поделиться|репост", re.IGNORECASE),
        re.compile(r"напиши|напишите\s+в\s+коммент", re.IGNORECASE),
        re.compile(r"что\s+думаешь|твое\s+мнение", re.IGNORECASE),
    ]

    def __init__(self, published_urls: Optional[set[str]] = None) -> None:
        self._published_urls = published_urls or set()

    def validate(self, post: Post) -> ValidationResult:
        result = ValidationResult()

        self._check_content(post, result)
        self._check_headline(post, result)
        self._check_tags(post, result)
        self._check_freshness(post, result, timedelta(hours=self.REPUBLISH_COOLDOWN_HOURS))
        self._check_russian_domains(post, result)
        self._check_cta(post, result)
        self._check_uniqueness(post, result)
        self._check_clickbait(post, result)

        if result.is_valid:
            logger.info("Post validation passed: %s", post.headline[:50])
        else:
            logger.warning("Post validation failed: %s", result.errors)

        return result

    def _check_content(self, post: Post, result: ValidationResult) -> None:
        if not post.content or not post.content.text:
            result.add_error("Post content is empty")
            return

        text = post.content.text
        word_count = len(text.split())

        if len(text) < self.MIN_LENGTH:
            result.add_error(f"Content too short: {len(text)} chars < {self.MIN_LENGTH}")
        if len(text) > self.MAX_LENGTH:
            result.add_error(f"Content too long: {len(text)} chars > {self.MAX_LENGTH}")

    def _check_headline(self, post: Post, result: ValidationResult) -> None:
        if not post.headline:
            result.add_error("Headline is empty")
        elif len(post.headline) < self.MIN_HEADLINE_LENGTH:
            result.add_warning(f"Headline too short: {len(post.headline)} chars")
        elif len(post.headline) > self.MAX_HEADLINE_LENGTH:
            result.add_warning(f"Headline too long: {len(post.headline)} chars")

    def _check_tags(self, post: Post, result: ValidationResult) -> None:
        for tag in post.tags:
            if len(tag) > 30:
                result.add_warning(f"Hashtag too long: #{tag}")

    def _check_freshness(self, post: Post, result: ValidationResult, max_age: timedelta) -> None:
        if post.created_at and datetime.now() - post.created_at > max_age:
            result.add_warning(f"Post content may be stale (created {post.created_at.isoformat()})")

    def _check_russian_domains(self, post: Post, result: ValidationResult) -> None:
        if not post.content or not post.content.text:
            return
        urls = re.findall(r"https?://([^/\s]+)", post.content.text)
        for domain in urls:
            domain_clean = domain.lower().replace("www.", "")
            if domain_clean in self.RUSSIAN_DOMAINS:
                result.add_warning(f"Russian state media source: {domain_clean}")

    def _check_cta(self, post: Post, result: ValidationResult) -> None:
        if not post.content or not post.content.text:
            return
        has_cta = any(p.search(post.content.text) for p in self.CTA_PATTERNS)
        if not has_cta:
            result.add_warning("No call-to-action found")

    def _check_uniqueness(self, post: Post, result: ValidationResult) -> None:
        if not post.content or not post.content.text:
            return
        text_hash = self._text_signature(post.content.text)
        if text_hash in self._published_urls:
            result.add_error("Duplicate content detected")

    def _check_clickbait(self, post: Post, result: ValidationResult) -> None:
        if not post.content or not post.content.text:
            return
        clickbait_words = [
            "шок", "сенсация", "невероятно", "все пропало",
            "никогда не", "ты не поверишь", "ужас",
        ]
        text_lower = post.content.text.lower()
        for word in clickbait_words:
            if word in text_lower:
                result.add_warning(f"Clickbait word detected: '{word}'")

    def _text_signature(self, text: str) -> str:
        return re.sub(r"\s+", "", text.lower())[:200]
