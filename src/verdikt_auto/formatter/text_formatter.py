"""Text formatter — converts Markdown to Telegram HTML, splits long posts, adds inline keyboards."""

import logging
import re
from typing import Optional

from verdikt_auto.core.models import Post, PostCategory

logger = logging.getLogger(__name__)

BRAND_HASHTAG = "вердикт"

HIGH_FREQ_KEYWORDS: list[str] = [
    "новости", "россия", "мир", "политика", "экономика",
]

MID_FREQ_KEYWORDS: list[str] = [
    "аналитика", "мнение", "прогноз", "исследование", "тренд",
]

LOW_FREQ_KEYWORDS: list[str] = [
    "детали", "факт", "цифра", "динамика", "изменение",
]


class TextFormatter:
    """Converts raw post content to Telegram-ready formatted text."""

    MAX_MESSAGE_LENGTH = 4096

    def format_post(self, post: Post) -> str:
        headline_html = self._markdown_to_html(post.headline) if post.headline else ""
        content_html = self._markdown_to_html(post.content.text) if post.content else ""

        parts: list[str] = []
        if headline_html:
            parts.append(f"<b>{headline_html}</b>")
            parts.append("")
        if content_html:
            parts.append(content_html)

        if parts:
            hashtags = self._generate_hashtag_mix(post)
            if hashtags:
                parts.append("")
                parts.append(" ".join(f"#{h}" for h in hashtags))

        full = "\n".join(parts)
        if len(full) > self.MAX_MESSAGE_LENGTH:
            return self._split_into_parts(full)[0]
        return full

    def format_post_parts(self, post: Post) -> list[str]:
        headline_html = self._markdown_to_html(post.headline) if post.headline else ""
        content_html = self._markdown_to_html(post.content.text) if post.content else ""

        parts: list[str] = []
        if headline_html:
            parts.append(f"<b>{headline_html}</b>")

        if content_html:
            paragraphs = content_html.split("\n\n")
            for p in paragraphs:
                stripped = p.strip()
                if stripped:
                    parts.append(stripped)

        hashtags = self._generate_hashtag_mix(post)
        if hashtags:
            parts.append(" ".join(f"#{h}" for h in hashtags))

        return self._chunk_parts(parts)

    def format_voice_intro(self, headline: str, duration_sec: int) -> str:
        minutes = duration_sec // 60
        seconds = duration_sec % 60
        return f"🎧 <b>Аудиоверсия</b> ({minutes}:{seconds:02d})\n{headline}"

    def build_inline_keyboard(self, sources: list[str]) -> str:
        if not sources:
            return ""
        buttons_html = " | ".join(
            f'<a href="{url}">🔗 {i + 1}</a>'
            for i, url in enumerate(sources[:5])
        )
        return buttons_html

    def _markdown_to_html(self, text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
        text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
        text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
        text = re.sub(r"(?<!\()https?://\S+", lambda m: f'<a href="{m.group(0)}">{m.group(0)[:50]}…</a>', text)
        return text

    def _generate_hashtag_mix(self, post: Post) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        def _add(t: str) -> None:
            clean = re.sub(r"[^\w]", "", t).lower()
            if clean and clean not in seen:
                seen.add(clean)
                tags.append(clean)

        _add(BRAND_HASHTAG)

        category_name = post.category.value if hasattr(post, "category") else PostCategory.GENERAL.value
        _add(category_name)

        for kw in HIGH_FREQ_KEYWORDS[:2]:
            _add(kw)

        if post.tags:
            for tag in post.tags[:2]:
                _add(tag)

        for kw in MID_FREQ_KEYWORDS[:2]:
            _add(kw)

        for kw in LOW_FREQ_KEYWORDS[:1]:
            _add(kw)

        text_lower = (post.content.text + post.headline).lower() if post.content else post.headline.lower()
        words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{4,}", text_lower)
        for word in words:
            if len(tags) >= 7:
                break
            _add(word)

        return tags[:7]

    def _split_into_parts(self, text: str) -> list[str]:
        paragraphs = text.split("\n\n")
        parts: list[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 > self.MAX_MESSAGE_LENGTH:
                if current:
                    parts.append(current.strip())
                current = para
            else:
                current = f"{current}\n\n{para}".strip()
        if current:
            parts.append(current.strip())
        return parts or [text[: self.MAX_MESSAGE_LENGTH]]

    def _chunk_parts(self, parts: list[str]) -> list[str]:
        chunks: list[str] = []
        current = ""
        for part in parts:
            if len(current) + len(part) + 1 > self.MAX_MESSAGE_LENGTH:
                if current:
                    chunks.append(current.strip())
                current = part
            else:
                current = f"{current}\n\n{part}".strip()
        if current:
            chunks.append(current.strip())
        return chunks or [""]
