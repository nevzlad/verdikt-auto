"""Monetization — ad management, affiliate tracking, premium gate."""

import json
import logging
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from verdikt_auto.core.ai_router import AIRouter

logger = logging.getLogger(__name__)


class AdsManager:
    """Intelligent ad placement with rotation and targeting."""

    def __init__(self, db_path: str = "data/ads.json") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ads: list[dict[str, Any]] = []
        self._impressions: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._ads = data.get("ads", [])
                self._impressions = data.get("impressions", {})
            except (json.JSONDecodeError, ValueError):
                pass

    def _save(self) -> None:
        data = {"ads": self._ads, "impressions": self._impressions}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def register(self, ad_id: str, text: str, weight: float = 1.0, category: str = "",
                 max_daily: int = 0) -> None:
        self._ads.append({
            "id": ad_id,
            "text": text,
            "weight": weight,
            "category": category,
            "max_daily": max_daily,
            "created": datetime.now().isoformat(),
        })
        self._save()
        logger.info("Ad registered: %s", ad_id)

    def select(self, post_category: str = "") -> Optional[str]:
        candidates = [a for a in self._ads if not a["category"] or a["category"] == post_category]
        if not candidates:
            candidates = self._ads
        if not candidates:
            return None
        weights = [a["weight"] for a in candidates]
        ad = random.choices(candidates, weights=weights, k=1)[0]
        ad_id = ad["id"]
        self._impressions[ad_id] = self._impressions.get(ad_id, 0) + 1
        self._save()
        if ad.get("max_daily", 0) > 0:
            today = datetime.now().strftime("%Y-%m-%d")
            daily_key = f"{ad_id}_{today}"
            daily_count = self._impressions.get(daily_key, 0)
            if daily_count >= ad["max_daily"]:
                return self._select_fallback(ad["category"])
        return ad["text"]

    def _select_fallback(self, category: str) -> Optional[str]:
        fallbacks = [a for a in self._ads if a["id"] != a.get("id") and a["category"] == category]
        if fallbacks:
            return random.choice(fallbacks)["text"]
        return None

    def insert(self, post_text: str, ad_text: str, position: str = "end") -> str:
        separator = "\n\n— — —\n\n"
        if position == "start":
            return f"{ad_text}\n\n{post_text}"
        if position == "middle":
            lines = post_text.split("\n")
            mid = len(lines) // 2
            lines.insert(mid, f"\n{ad_text}\n")
            return "\n".join(lines)
        return f"{post_text}{separator}{ad_text}"

    def stats(self) -> dict[str, Any]:
        return {
            "total_ads": len(self._ads),
            "total_impressions": sum(self._impressions.values()),
            "active_categories": list({a["category"] for a in self._ads if a["category"]}),
        }


class AffiliateTracker:
    """Tracks affiliate links and conversions."""

    def __init__(self, path: str = "data/affiliate.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._links: dict[str, dict[str, Any]] = {}
        self._clicks: dict[str, int] = {}
        self._conversions: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._links = data.get("links", {})
                self._clicks = data.get("clicks", {})
                self._conversions = data.get("conversions", {})
            except (json.JSONDecodeError, ValueError):
                pass

    def _save(self) -> None:
        data = {"links": self._links, "clicks": self._clicks, "conversions": self._conversions}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def register(self, keyword: str, url: str, campaign: str = "", commission: float = 0.0) -> None:
        self._links[keyword.lower()] = {
            "url": url,
            "campaign": campaign,
            "commission": commission,
        }
        self._save()
        logger.info("Affiliate link registered: %s -> %s", keyword, url)

    def enrich(self, text: str) -> str:
        for keyword, info in self._links.items():
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            if pattern.search(text):
                replacement = f"[{keyword}]({info['url']})"
                text = pattern.sub(replacement, text, count=1)
        return text

    def track_click(self, link_id: str) -> None:
        self._clicks[link_id] = self._clicks.get(link_id, 0) + 1
        self._save()

    def track_conversion(self, link_id: str) -> None:
        self._conversions[link_id] = self._conversions.get(link_id, 0) + 1
        self._save()

    def report(self) -> list[dict[str, Any]]:
        return [
            {
                "keyword": kw,
                "url": info["url"],
                "campaign": info["campaign"],
                "clicks": self._clicks.get(kw, 0),
                "conversions": self._conversions.get(kw, 0),
                "cr": round(
                    self._conversions.get(kw, 0) / max(self._clicks.get(kw, 0), 1) * 100, 2
                ),
            }
            for kw, info in self._links.items()
        ]


class PremiumGate:
    """Gates premium content behind subscription check."""

    def __init__(self, path: str = "data/premium.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._subscribers: dict[str, dict[str, Any]] = {}
        self._exclusive: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._subscribers = data.get("subscribers", {})
                self._exclusive = data.get("exclusive", [])
            except (json.JSONDecodeError, ValueError):
                pass

    def _save(self) -> None:
        data = {"subscribers": self._subscribers, "exclusive": self._exclusive}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def subscribe(self, user_id: str, until: Optional[str] = None) -> None:
        expiry = until or (datetime.now() + timedelta(days=30)).isoformat()
        self._subscribers[user_id] = {
            "since": datetime.now().isoformat(),
            "until": expiry,
        }
        self._save()
        logger.info("Premium activated for %s until %s", user_id[:8], expiry)

    def unsubscribe(self, user_id: str) -> None:
        self._subscribers.pop(user_id, None)
        self._save()
        logger.info("Premium deactivated for %s", user_id[:8])

    def check(self, user_id: str) -> bool:
        info = self._subscribers.get(user_id)
        if not info:
            return False
        if info["until"] < datetime.now().isoformat():
            self.unsubscribe(user_id)
            return False
        return True

    def add_exclusive(self, content_id: str, title: str, text: str) -> None:
        self._exclusive.append({
            "id": content_id,
            "title": title,
            "text": text,
            "created": datetime.now().isoformat(),
        })
        self._save()
        logger.info("Exclusive content added: %s", content_id)

    def get_exclusive(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        if self.check(user_id):
            return self._exclusive[-limit:]
        return []

    def count(self) -> int:
        active = 0
        now = datetime.now().isoformat()
        for info in self._subscribers.values():
            if info["until"] >= now:
                active += 1
        return active
