"""Headline optimizer — generates variants, scores them, selects the best."""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import HeadlineType, HeadlineVariant, Post, PostCategory

logger = logging.getLogger(__name__)


class HeadlineScorer:
    """Scores headline variants across multiple dimensions."""

    def score(self, headline: str) -> float:
        score = 0.0
        score += self._length_score(headline) * 0.20
        score += self._digit_score(headline) * 0.15
        score += self._trigger_score(headline) * 0.20
        score += self._question_score(headline) * 0.15
        score += self._uniqueness_score(headline) * 0.10
        score += self._keyword_score(headline) * 0.20
        return round(min(score, 1.0), 4)

    def _length_score(self, headline: str) -> float:
        n = len(headline)
        if 30 <= n <= 80:
            return 1.0
        if 20 <= n < 30 or 80 < n <= 100:
            return 0.6
        if 10 <= n < 20 or 100 < n <= 120:
            return 0.3
        return 0.0

    def _digit_score(self, headline: str) -> float:
        digits = re.findall(r"\d+", headline)
        if digits:
            return min(len(digits) * 0.3, 1.0)
        return 0.0

    def _trigger_score(self, headline: str) -> float:
        triggers = ["почему", "как", "что будет", "зачем", "секрет", "правда", "главное"]
        text_lower = headline.lower()
        for t in triggers:
            if t in text_lower:
                return 0.8
        return 0.2

    def _question_score(self, headline: str) -> float:
        if headline.endswith("?"):
            return 1.0
        if headline.endswith("!"):
            return 0.7
        return 0.3

    def _uniqueness_score(self, headline: str) -> float:
        words = set(re.findall(r"[а-яёА-ЯЁa-zA-Z]{4,}", headline.lower()))
        ratio = len(words) / max(len(headline.split()), 1)
        if ratio > 0.7:
            return 1.0
        if ratio > 0.5:
            return 0.6
        return 0.3

    def _keyword_score(self, headline: str) -> float:
        seo_keywords = ["россия", "мир", "экономика", "технологии", "политика", "наука", "общество"]
        text_lower = headline.lower()
        found = sum(1 for kw in seo_keywords if kw in text_lower)
        return min(found / 2, 1.0)


class HeadlineHistory:
    """Tracks selected headlines to avoid repetition."""

    def __init__(self, path: str = "data/headline_history.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._history: list[dict[str, object]] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._history = data if isinstance(data, list) else []
            except (json.JSONDecodeError, ValueError):
                self._history = []

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._history[-200:], ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, headline: str, score: float) -> None:
        self._history.append({"headline": headline, "score": score})
        self._save()

    def is_repeat(self, headline: str, threshold_days: int = 7) -> bool:
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=threshold_days)
        return any(
            h["headline"].lower() == headline.lower()
            for h in self._history[-100:]
        )


class HeadlineSelector:
    """Selects the best headline from variants using HeadlineScorer."""

    def __init__(self) -> None:
        self.scorer = HeadlineScorer()
        self.history = HeadlineHistory()

    def select(self, variants: list[HeadlineVariant]) -> HeadlineVariant:
        if not variants:
            return HeadlineVariant(text="", type=HeadlineType.paradox, score=0.0)
        for v in variants:
            v.score = self.scorer.score(v.text)
        scored = sorted(variants, key=lambda v: v.score, reverse=True)
        best = scored[0]
        if self.history.is_repeat(best.text):
            for alt in scored[1:]:
                if not self.history.is_repeat(alt.text):
                    best = alt
                    break
        self.history.record(best.text, best.score)
        return best


class HeadlineOptimizer:
    """Generates, scores and selects optimal headlines for a post."""

    def __init__(self, router: AIRouter) -> None:
        self.router = router
        self.scorer = HeadlineScorer()
        self.selector = HeadlineSelector()

    async def generate_variants(self, post: Post) -> list[HeadlineVariant]:
        headline_prompt = self._build_prompt(post)
        result = await self.router.route("generate_headline", prompt=headline_prompt)
        text = result.text if hasattr(result, "text") else str(result)
        return self._parse_variants(text)

    def _build_prompt(self, post: Post) -> str:
        topic = post.topic or post.headline
        category = post.category.value if hasattr(post, "category") else PostCategory.GENERAL.value
        return (
            f"Создай 3 варианта заголовка для Telegram-поста.\n"
            f"Тема: {topic}\n"
            f"Категория: {category}\n\n"
            f"Требования:\n"
            f"- Не длиннее 80 символов\n"
            f"- На русском\n"
            f"- Без кликбейта\n"
            f"- Разные форматы\n\n"
            f"Формат ответа:\n"
            f"ПАРАДОКС: <текст> | <оценка 0-1>\n"
            f"ВОПРОС: <текст> | <оценка 0-1>\n"
            f"ШОК-ФАКТ: <текст> | <оценка 0-1>"
        )

    def _parse_variants(self, text: str) -> list[HeadlineVariant]:
        variants: list[HeadlineVariant] = []
        type_map = {
            "ПАРАДОКС": HeadlineType.paradox,
            "ВОПРОС": HeadlineType.question,
            "ШОК-ФАКТ": HeadlineType.shock_fact,
        }
        for line in text.strip().split("\n"):
            line = line.strip()
            for prefix, htype in type_map.items():
                if line.upper().startswith(prefix):
                    rest = line[len(prefix):].lstrip(":").strip()
                    headline_text, score = rest, 0.0
                    if "|" in rest:
                        headline_text, score_str = rest.rsplit("|", 1)
                        headline_text = headline_text.strip()
                        try:
                            score = float(score_str.strip())
                        except ValueError:
                            score = 0.0
                    headline_text = headline_text.strip().strip('"').strip("'")
                    if headline_text:
                        scored = self.scorer.score(headline_text)
                        variants.append(HeadlineVariant(
                            text=headline_text,
                            type=htype,
                            score=max(score, scored),
                        ))
                    break
        return variants

    async def optimize(self, post: Post) -> Post:
        variants = await self.generate_variants(post)
        if variants:
            best = self.selector.select(variants)
            post.headline = best.text
            from verdikt_auto.core.models import HeadlineVariant as HV
            post.headlines = variants
        return post
