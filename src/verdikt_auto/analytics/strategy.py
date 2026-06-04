"""Strategy — adjusts ranker weights, generates reports, predicts viral topics."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import GeneratedContent, RankedTopic

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS: dict[str, float] = {
    "w1": 0.20,
    "w2": 0.15,
    "w3": 0.20,
    "w4": 0.10,
    "w5": 0.15,
    "w6": 0.10,
    "w7": 0.10,
}


class StrategyAdjuster:
    """Adjusts ranker weights based on historical performance."""

    def __init__(self, weights_path: str = "data/strategy_weights.json") -> None:
        self._path = Path(weights_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._weights: dict[str, float] = dict(DEFAULT_WEIGHTS)
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._weights.update(data)
            except (json.JSONDecodeError, ValueError):
                pass

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._weights, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_weights(self) -> dict[str, float]:
        return dict(self._weights)

    def adjust(self, performance: dict[str, dict[str, float]]) -> dict[str, float]:
        changes: dict[str, float] = {}
        for weight_key, metrics in performance.items():
            current = self._weights.get(weight_key, 0.10)
            if weight_key not in self._weights:
                continue
            engagement = metrics.get("avg_engagement", 0.5)
            if engagement > 0.7:
                new_val = min(current * 1.1, 0.35)
                changes[weight_key] = new_val - current
                self._weights[weight_key] = new_val
            elif engagement < 0.3:
                new_val = max(current * 0.9, 0.05)
                changes[weight_key] = new_val - current
                self._weights[weight_key] = new_val
        if changes:
            self._normalize()
            self._save()
            logger.info("Weights adjusted: %s", {k: round(v, 3) for k, v in changes.items()})
        return changes

    def _normalize(self) -> None:
        total = sum(self._weights.values())
        if total > 0:
            for k in self._weights:
                self._weights[k] = round(self._weights[k] / total, 4)


class WeeklyReportGenerator:
    """Generates admin reports with AI-powered insights."""

    def __init__(self, router: Optional[AIRouter] = None) -> None:
        self.router = router

    async def generate(self, stats: dict[str, Any], topics: list[RankedTopic]) -> str:
        if self.router:
            return await self._ai_report(stats, topics)
        return self._template_report(stats, topics)

    def _template_report(self, stats: dict[str, Any], topics: list[RankedTopic]) -> str:
        lines = [
            "📊 <b>Еженедельный отчёт VERDIKT</b>\n",
            f"📅 Неделя: {(datetime.now() - timedelta(days=7)).strftime('%d.%m')} — {datetime.now().strftime('%d.%m.%Y')}",
            f"📝 Постов: {stats.get('posts_count', 0)}",
            f"👁 Просмотров (7д): {stats.get('views_last_7d', 0)}",
            f"📈 Вовлечённость: {stats.get('avg_engagement', 0)}%",
            f"👥 Новых подписчиков: {stats.get('new_subscribers', 0)}",
            "",
            "🏆 <b>Топ-5 тем недели:</b>",
        ]
        for i, t in enumerate(topics[:5], 1):
            lines.append(f"{i}. {t.title} — {t.score:.2f}")
        return "\n".join(lines)

    async def _ai_report(self, stats: dict[str, Any], topics: list[RankedTopic]) -> str:
        top_topics = "\n".join(f"- {t.title} (score: {t.score:.2f})" for t in topics[:5])
        prompt = (
            f"Сгенерируй еженедельный отчёт для Telegram-канала на русском.\n\n"
            f"Статистика:\n"
            f"- Постов: {stats.get('posts_count', 0)}\n"
            f"- Просмотров: {stats.get('views_last_7d', 0)}\n"
            f"- Вовлечённость: {stats.get('avg_engagement', 0)}%\n\n"
            f"Топ-5 тем:\n{top_topics}\n\n"
            f"Добавь: анализ трендов, рекомендации, прогноз на следующую неделю."
        )
        result = await self.router.route("generate_post", prompt=prompt)
        return result.text if isinstance(result, GeneratedContent) else str(result)


class PredictiveTopicFinder:
    """Predicts potentially viral topics using AI."""

    def __init__(self, router: AIRouter) -> None:
        self.router = router

    async def predict(self, recent_topics: list[RankedTopic], chat_comments: list[str]) -> list[dict[str, Any]]:
        sample_topics = "\n".join(f"- {t.title} (score: {t.score:.2f})" for t in recent_topics[:10])
        sample_comments = "\n".join(chat_comments[:20])[:2000]
        prompt = (
            f"Проанализируй тренды и предложи 3 темы, которые станут вирусными.\n\n"
            f"Последние темы:\n{sample_topics}\n\n"
            f"Комментарии аудитории:\n{sample_comments}\n\n"
            f"Для каждой темы укажи:\n"
            f"- название\n"
            f"- прогноз просмотров\n"
            f"- почему взлетит\n"
            f"- рекомендуемый формат"
        )
        result = await self.router.route("generate_post", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return self._parse_predictions(text)

    def _parse_predictions(self, text: str) -> list[dict[str, Any]]:
        predictions: list[dict[str, Any]] = []
        blocks = text.strip().split("\n\n")
        for block in blocks:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if not lines:
                continue
            pred: dict[str, Any] = {"raw": block[:200]}
            for line in lines:
                if "назван" in line.lower() or line.startswith("1.") or line.startswith("2.") or line.startswith("3."):
                    pred["title"] = line.split(":", 1)[-1].strip().lstrip("0123456789.").strip()
                elif "прогноз" in line.lower() or "просмотров" in line.lower():
                    pred["predicted_views"] = line.split(":", 1)[-1].strip()
                elif "почему" in line.lower():
                    pred["reason"] = line.split(":", 1)[-1].strip()
                elif "формат" in line.lower():
                    pred["format"] = line.split(":", 1)[-1].strip()
            if "title" in pred:
                predictions.append(pred)
        return predictions[:3]
