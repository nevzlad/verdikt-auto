"""Chat analyzer — reads audience chats, extracts topics, detects pain points."""

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import GeneratedContent

logger = logging.getLogger(__name__)


class ChatReader:
    """Reads channel comments via Telethon."""

    def __init__(self, api_id: int = 0, api_hash: str = "") -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._client: Optional[Any] = None

    async def read_comments(self, chat_id: str, limit: int = 100) -> list[str]:
        if not self._api_id or not self._api_hash:
            logger.warning("Telethon not configured — returning empty")
            return []

        try:
            from telethon import TelegramClient
            async with TelegramClient("chat_reader_session", self._api_id, self._api_hash) as client:
                messages = await client.get_messages(chat_id, limit=limit)
                texts = [m.text for m in messages if m.text]
                logger.info("Read %d comments from %s", len(texts), chat_id)
                return texts
        except ImportError:
            logger.warning("Telethon not installed")
            return []
        except Exception as exc:
            logger.error("Failed to read comments: %s", exc)
            return []


class TopicExtractor:
    """Extracts topics from text using TF-IDF + KMeans (sklearn)."""

    MIN_TERM_LENGTH = 4
    MAX_FEATURES = 1000

    def __init__(self) -> None:
        self._sklearn_available = self._check_sklearn()

    def _check_sklearn(self) -> bool:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401
            from sklearn.cluster import KMeans  # noqa: F401
            return True
        except ImportError:
            return False

    def extract_keywords(self, texts: list[str], top_n: int = 10) -> list[tuple[str, float]]:
        if not texts:
            return []

        if self._sklearn_available:
            return self._tfidf_keywords(texts, top_n)
        return self._freq_keywords(texts, top_n)

    def _tfidf_keywords(self, texts: list[str], top_n: int) -> list[tuple[str, float]]:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vec = TfidfVectorizer(
                max_features=self.MAX_FEATURES,
                stop_words=self._get_stop_words(),
                ngram_range=(1, 2),
                min_df=1,
            )
            matrix = vec.fit_transform(texts)
            scores = matrix.sum(axis=0).A1
            terms = vec.get_feature_names_out()
            ranked = sorted(zip(terms, scores), key=lambda x: x[1], reverse=True)
            return [(term, round(score, 4)) for term, score in ranked[:top_n]]
        except Exception as exc:
            logger.warning("TF-IDF failed: %s", exc)
            return self._freq_keywords(texts, top_n)

    def _freq_keywords(self, texts: list[str], top_n: int) -> list[tuple[str, float]]:
        counter: Counter[str] = Counter()
        stop_words = self._get_stop_words()
        for text in texts:
            words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{" + str(self.MIN_TERM_LENGTH) + r",}", text.lower())
            for w in words:
                if w not in stop_words:
                    counter[w] += 1
        total = max(sum(counter.values()), 1)
        return [(w, round(c / total, 4)) for w, c in counter.most_common(top_n)]

    def cluster_topics(self, texts: list[str], n_clusters: int = 5) -> list[dict[str, Any]]:
        if not self._sklearn_available or len(texts) < n_clusters:
            return [{"topic": kw, "score": sc} for kw, sc in self.extract_keywords(texts, 10)]

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.cluster import KMeans
            vec = TfidfVectorizer(max_features=self.MAX_FEATURES, stop_words=self._get_stop_words())
            matrix = vec.fit_transform(texts)
            km = KMeans(n_clusters=min(n_clusters, len(texts)), random_state=42, n_init=5)
            labels = km.fit_predict(matrix)
            clusters: dict[int, list[str]] = {}
            for text, label in zip(texts, labels):
                clusters.setdefault(int(label), []).append(text)
            results = []
            for label, cluster_texts in clusters.items():
                kws = self._tfidf_keywords(cluster_texts, 3)
                results.append({
                    "cluster": int(label),
                    "size": len(cluster_texts),
                    "keywords": [kw for kw, _ in kws],
                })
            return results
        except Exception as exc:
            logger.warning("KMeans clustering failed: %s", exc)
            return []

    def _get_stop_words(self) -> list[str]:
        return [
            "это", "что", "как", "так", "все", "быть", "его", "ее", "они",
            "когда", "где", "кто", "него", "нее", "них", "том", "тем",
            "чтобы", "можно", "только", "если", "еще", "уже", "будет",
            "вот", "даже", "очень", "просто", "теперь", "потом",
        ]


class PainPointDetector:
    """Detects audience pain points from comments using AI Router."""

    def __init__(self, router: AIRouter) -> None:
        self.router = router

    async def detect(self, comments: list[str]) -> list[dict[str, Any]]:
        if not comments:
            return []
        sample = "\n".join(comments[:50])
        prompt = (
            f"Проанализируй комментарии аудитории и выдели 3-5 основных болей\n"
            f"(проблем, недовольств, желаний). Для каждой укажи:\n"
            f"- боль (коротко)\n"
            f"- частота (высокая/средняя/низкая)\n"
            f"- эмоциональная окраска\n"
            f"- предложение по контенту\n\n"
            f"Комментарии:\n{sample[:3000]}"
        )
        result = await self.router.route("analyze_chat", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return self._parse_pain_points(text)

    def _parse_pain_points(self, text: str) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        blocks = re.split(r"\n\s*\n", text.strip())
        for block in blocks:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if not lines:
                continue
            point: dict[str, Any] = {"raw": block}
            for line in lines:
                if "боль" in line.lower() or line.startswith("-"):
                    point["pain"] = line.lstrip("- ").strip()
                elif "частот" in line.lower():
                    point["frequency"] = line.split(":")[-1].strip()
                elif "эмоц" in line.lower():
                    point["sentiment"] = line.split(":")[-1].strip()
                elif "предлож" in line.lower():
                    point["suggestion"] = line.split(":")[-1].strip()
            if "pain" in point:
                points.append(point)
        return points[:5]


class PriorityUpdater:
    """Auto-adds discovered topics to priority list."""

    def __init__(self, path: str = "data/priority_topics.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._topics: list[str] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._topics = data if isinstance(data, list) else []
            except (json.JSONDecodeError, ValueError):
                self._topics = []

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._topics, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, topic: str) -> None:
        if topic and topic not in self._topics:
            self._topics.append(topic)
            self._save()
            logger.info("Priority topic added: %s", topic)

    def add_batch(self, topics: list[str]) -> None:
        added = 0
        for t in topics:
            if t and t not in self._topics:
                self._topics.append(t)
                added += 1
        if added:
            self._save()
            logger.info("Added %d priority topics", added)

    def get_all(self) -> list[str]:
        return list(self._topics)

    def remove(self, topic: str) -> None:
        if topic in self._topics:
            self._topics.remove(topic)
            self._save()
