"""Topic ranking engine — scores topics by weighted multi-factor formula with debug output."""

import logging
import math
import re
from datetime import datetime
from typing import Optional

from verdikt_auto.core.models import RankedTopic
from verdikt_auto.prompt.history_manager import HistoryManager
from verdikt_auto.scanner.models import Topic

logger = logging.getLogger(__name__)

CHANNEL_FOCUS_KEYWORDS = [
    "СВО", "Украина", "США", "Иран", "Израиль",
    "экономика", "геополитика", "санкции", "БРИКС",
]

SEMANTIC_CORE = ["аналитика", "разбор", "вердикт", "OSINT", "фейк"]

EMOTIONAL_MARKERS = ["шок", "скандал", "прорыв", "разоблачение", "сенсация"]
URGENCY_MARKERS = ["срочно", "только что", "сейчас", "экстренно", "breaking"]


class TopicRanker:
    """Ranks topics by weighted multi-factor formula.

    final_score = (
        w1 * normalize(views, 'log') +
        w2 * normalize(engagement) +
        w3 * relevance_score +
        w4 * continuity_bonus +
        w5 * novelty_score +
        w6 * viral_potential +
        w7 * seo_score
    )

    Returns top-N topics (N = posts_per_day + 5 reserve).
    """

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
        history: Optional[HistoryManager] = None,
    ) -> None:
        self.weights = weights or {
            "w1": 0.20,
            "w2": 0.15,
            "w3": 0.20,
            "w4": 0.10,
            "w5": 0.15,
            "w6": 0.10,
            "w7": 0.10,
        }
        self._history = history or HistoryManager()

    @staticmethod
    def normalize(value: float, max_value: float, method: str = "minmax") -> float:
        if max_value <= 0:
            return 0.0
        if method == "log":
            return min(math.log1p(value) / math.log1p(max_value), 1.0)
        return min(value / max_value, 1.0)

    def _relevance_score(self, topic: Topic) -> float:
        """Cosine similarity via TF-IDF between topic and channel focus vector."""
        topic_text = f"{topic.title} {' '.join(topic.keywords)}"
        focus_text = " ".join(CHANNEL_FOCUS_KEYWORDS)

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=500)
            tfidf = vec.fit_transform([focus_text, topic_text])
            sim = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]
            return round(float(sim), 4)
        except ImportError:
            pass

        focus_set = set(w.lower() for w in CHANNEL_FOCUS_KEYWORDS)
        topic_words = set(re.findall(r"[а-яёА-ЯЁa-zA-Z]{3,}", topic_text.lower()))
        overlap = focus_set & topic_words
        if not overlap:
            return 0.0
        return round(len(overlap) / max(len(focus_set | topic_words), 1), 4)

    def _continuity_bonus(self, topic: Topic) -> float:
        """+0.3 if topic relates to an unresolved announcement (keyword overlap)."""
        announcements = self._history.get_unresolved_announcements()
        topic_keywords = set(k.lower() for k in topic.keywords + [topic.title])

        for ann in announcements:
            ann_words = set(re.findall(r"[а-яёА-ЯЁa-zA-Z]{3,}", ann.get("title", "").lower()))
            overlap = topic_keywords & ann_words
            if len(overlap) >= 2:
                logger.debug("Continuity bonus for '%s' via announcement '%s'", topic.title, ann.get("title"))
                return 0.8
        return 0.5

    def _novelty_score(self, topic: Topic) -> float:
        """Age factor × frequency penalty: age = 1 - age_hours/48, freq = 1/(count_in_last_7_days + 1)."""
        age_hours = (datetime.now() - topic.published_at).total_seconds() / 3600
        age_factor = max(0.0, 1.0 - age_hours / 48.0)
        count = self._history.count_in_last_7_days(topic.keywords)
        freq_factor = 1.0 / (count + 1)
        return round(age_factor * freq_factor, 4)

    def _viral_potential(self, topic: Topic) -> float:
        """Heuristic scoring from title features."""
        score = 0.0
        title = topic.title

        if "?" in title:
            score += 0.1
        if re.search(r"\d", title):
            score += 0.1

        title_lower = title.lower()
        for marker in EMOTIONAL_MARKERS:
            if marker in title_lower:
                score += 0.15
                break
        for marker in URGENCY_MARKERS:
            if marker in title_lower:
                score += 0.1
                break

        source_boost = {
            "youtube": 0.2, "trends": 0.2, "telegram": 0.1,
            "rss": 0.0,
        }
        score += source_boost.get(topic.source, 0.0)

        return round(min(score, 1.0), 4)

    def _seo_score(self, topic: Topic) -> float:
        """Presence of semantic core keywords in title + keywords."""
        text = (topic.title + " " + " ".join(topic.keywords)).lower()
        score = 0.0
        for kw in SEMANTIC_CORE:
            if kw.lower() in text:
                score += 0.2
        return round(min(score, 1.0), 4)

    def _build_explanation(
        self,
        title: str,
        nv: float, ne: float, rs: float, cb: float,
        ns: float, vp: float, ss: float, total: float,
    ) -> str:
        parts = [
            f"views(log)={nv:.3f}×w1={self.weights['w1']}",
            f"eng={ne:.3f}×w2={self.weights['w2']}",
            f"rel={rs:.3f}×w3={self.weights['w3']}",
            f"cont={cb:.3f}×w4={self.weights['w4']}",
            f"nov={ns:.3f}×w5={self.weights['w5']}",
            f"viral={vp:.3f}×w6={self.weights['w6']}",
            f"seo={ss:.3f}×w7={self.weights['w7']}",
        ]
        return f"score={total:.4f} | {' + '.join(parts)}"

    def rank(
        self,
        topics: list[Topic],
        posts_per_day: int = 7,
        debug: bool = False,
    ) -> list[RankedTopic]:
        if not topics:
            return []

        max_views = max((t.views for t in topics), default=1) or 1
        max_engagement = max((t.engagement for t in topics), default=1) or 1

        ranked: list[RankedTopic] = []
        for topic in topics:
            nv = self.normalize(float(topic.views), float(max_views), "log")
            ne = self.normalize(topic.engagement, float(max_engagement), "minmax")
            rs = self._relevance_score(topic)
            cb = self._continuity_bonus(topic)
            ns = self._novelty_score(topic)
            vp = self._viral_potential(topic)
            ss = self._seo_score(topic)

            total = (
                self.weights["w1"] * nv
                + self.weights["w2"] * ne
                + self.weights["w3"] * rs
                + self.weights["w4"] * cb
                + self.weights["w5"] * ns
                + self.weights["w6"] * vp
                + self.weights["w7"] * ss
            )

            rt = RankedTopic(
                topic_id=topic.id,
                title=topic.title,
                score=round(total, 4),
                normalized_views=nv,
                normalized_engagement=ne,
                relevance_score=rs,
                continuity_bonus=cb,
                novelty_score=ns,
                viral_potential=vp,
                seo_score=ss,
            )
            if debug:
                rt.explanation = self._build_explanation(
                    topic.title, nv, ne, rs, cb, ns, vp, ss, total,
                )
            ranked.append(rt)

        ranked.sort(key=lambda r: r.score, reverse=True)

        top_n = posts_per_day + 5
        result = ranked[:top_n]
        logger.info(
            "Ranked %d topics → top %d, first: %s (score=%.4f)",
            len(ranked), len(result),
            result[0].title if result else "N/A",
            result[0].score if result else 0,
        )
        if debug:
            for r in result:
                logger.debug("  %s — %s", r.title[:50], r.explanation)
        return result
