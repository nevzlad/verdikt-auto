"""Post parser — splits raw LLM output into structured Post objects."""

import logging
import re
from typing import Optional

from verdikt_auto.core.models import (
    GeneratedContent,
    HeadlineType,
    HeadlineVariant,
    Post,
    PostCategory,
    ProviderName,
    TaskType,
)

logger = logging.getLogger(__name__)


class MissingBlockError(Exception):
    """Raised when a required block is missing from parsed output."""


class PostParser:
    """Splits multi-post LLM output into individual Post objects."""

    POST_SEPARATORS = [
        re.compile(r"^#{1,3}\s*\[?ВЕРДИКТ\s*\d+\s*/\s*\d+\]?", re.MULTILINE | re.IGNORECASE),
        re.compile(r"\n---+\n"),
        re.compile(r"^#{1,3}\s*\[?VERDIKT\s*\d+\s*/\s*\d+\]?", re.MULTILINE | re.IGNORECASE),
    ]
    BLOCK_EMOJIS = {"🔍", "📊", "💡", "📌", "🔗", "📝", "📰", "⚡"}

    def parse(self, response: str, provider: ProviderName = ProviderName.groq, model: str = "") -> list[Post]:
        """Parse a multi-post LLM response into Post objects."""
        raw_posts = self._split_posts(response)
        posts: list[Post] = []
        for i, raw in enumerate(raw_posts):
            try:
                post = self._parse_single(raw, f"post_{i}", provider, model)
                posts.append(post)
            except MissingBlockError as exc:
                logger.warning("Skipping post %d: %s", i, exc)
                continue
        if not posts:
            logger.error("No valid posts parsed from response")
        return posts

    def _split_posts(self, response: str) -> list[str]:
        """Split concatenated LLM output by VERSICT markers or --- separators."""
        for sep in self.POST_SEPARATORS:
            parts = sep.split(response)
            if len(parts) > 1:
                return [p.strip() for p in parts if p.strip()]
        return [response.strip()]

    def _parse_single(self, text: str, post_id: str, provider: ProviderName, model: str) -> Post:
        """Parse a single post block into a Post object."""
        blocks = self._extract_blocks(text)
        if not blocks:
            raise MissingBlockError("No emoji-marked blocks found")

        headline = self._extract_headline(text)
        bridge = self._extract_bridge(text)
        category = self._detect_category(text)
        tags = self._extract_tags(text)

        content_text = self._assemble_content(blocks, headline, bridge)
        content = GeneratedContent(
            task_type=TaskType.generate_post,
            provider=provider,
            text=content_text,
            model=model,
        )

        return Post(
            id=post_id,
            topic=headline or text[:50],
            content=content,
            headline=headline,
            tags=tags,
            category=category,
        )

    def _extract_blocks(self, text: str) -> dict[str, str]:
        """Extract emoji-marked blocks from text."""
        blocks: dict[str, str] = {}
        lines = text.split("\n")
        current_emoji: Optional[str] = None
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            first_char = stripped[0]
            if first_char in self.BLOCK_EMOJIS:
                if current_emoji and current_lines:
                    blocks[current_emoji] = "\n".join(current_lines).strip()
                current_emoji = first_char
                current_lines = [stripped]
            elif current_emoji:
                current_lines.append(stripped)

        if current_emoji and current_lines:
            blocks[current_emoji] = "\n".join(current_lines).strip()

        # Normalize emoji keys
        emoji_map = {"📝": "📊", "📰": "🔍", "⚡": "💡"}
        for old, new in emoji_map.items():
            if old in blocks:
                blocks[new] = blocks.pop(old)

        return blocks

    def _extract_headline(self, text: str) -> str:
        """Extract headline from first line or h1/h2."""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        if not lines:
            return ""
        first = lines[0]
        if first.startswith("#"):
            first = re.sub(r"^#+\s*", "", first)
        if first.startswith("🏆") or first.startswith("📰"):
            first = first[1:].strip()
        return first[:120]

    def _extract_bridge(self, text: str) -> str:
        """Extract bridge/announcement paragraph — lines with → or <b> or keywords."""
        lines = text.split("\n")
        bridge_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "→" in stripped or "<b>" in stripped:
                bridge_lines.append(stripped)
            elif re.search(r"(анонс|ранее|напомним|продолжение|читайте|смотрите)", stripped, re.IGNORECASE):
                bridge_lines.append(stripped)
        return " ".join(bridge_lines)[:200]

    def _detect_category(self, text: str) -> PostCategory:
        """Detect post category from keywords."""
        keywords = re.findall(r"\w+", text.lower())
        return PostCategory.from_keywords(keywords)

    def _extract_tags(self, text: str) -> list[str]:
        """Extract hashtags from text."""
        return list(set(re.findall(r"#(\w+)", text)))

    def _assemble_content(self, blocks: dict[str, str], headline: str, bridge: str) -> str:
        """Assemble final content from blocks."""
        parts: list[str] = []
        if headline:
            parts.append(f"🏆 {headline}")
        for emoji in ["🔍", "📊", "💡", "📌", "🔗"]:
            if emoji in blocks:
                parts.append(blocks[emoji])
        if bridge:
            parts.append(f"🔗 {bridge}")
        return "\n\n".join(parts)


class HeadlineExtractor:
    """Extracts and scores headline variants from generated text."""

    def extract(self, text: str) -> list[HeadlineVariant]:
        """Parse headline variants with heuristic scoring."""
        variants: list[HeadlineVariant] = []
        lines = text.strip().split("\n")
        for line in lines:
            cleaned = line.strip().lstrip("0123456789").lstrip(".)- ").strip()
            if not cleaned or len(cleaned) > 150:
                continue
            score = self._score_heuristic(cleaned)
            vtype = self._detect_type(cleaned)
            variants.append(HeadlineVariant(text=cleaned, type=vtype, score=score))
        return sorted(variants, key=lambda v: v.score, reverse=True)

    def _score_heuristic(self, headline: str) -> float:
        """Score a headline 0.0-1.0 based on engagement factors."""
        score = 0.5
        if any(c in headline for c in ("?", "!")):
            score += 0.15
        if re.search(r"\d+", headline):
            score += 0.1
        if "—" in headline or "–" in headline:
            score += 0.1
        for word in ("как", "почему", "что", "кто", "где", "зачем"):
            if headline.lower().startswith(word):
                score += 0.1
        length = len(headline)
        if 30 <= length <= 80:
            score += 0.1
        elif length > 120:
            score -= 0.2
        clickbait = ("шок", "сенсация", "невероятно", "все пропало", "ты не поверишь")
        if any(w in headline.lower() for w in clickbait):
            score -= 0.2
        return max(0.0, min(1.0, score))

    def _detect_type(self, headline: str) -> HeadlineType:
        """Detect headline type."""
        if headline.endswith("?"):
            return HeadlineType.question
        if headline.endswith("!"):
            return HeadlineType.shock_fact
        if "—" in headline or "–" in headline:
            return HeadlineType.paradox
        return HeadlineType.paradox
