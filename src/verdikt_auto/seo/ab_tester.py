"""A/B tester for headline variants."""

import logging
import random
from typing import Optional

from verdikt_auto.core.models import Post

logger = logging.getLogger(__name__)


class ABTester:
    """Manages A/B testing of headline variants."""

    def __init__(self) -> None:
        self.results: dict[str, dict] = {}

    def select_variant(self, post_id: str, headlines: list[str]) -> tuple[str, str]:
        """Pick a control and test variant. Returns (control, test)."""
        if len(headlines) < 2:
            variant = headlines[0] if headlines else "No headline"
            return variant, variant

        random.seed(post_id)
        shuffled = headlines.copy()
        random.shuffle(shuffled)
        return shuffled[0], shuffled[1]

    def record_result(self, post_id: str, variant: str, views: int, clicks: int) -> None:
        if post_id not in self.results:
            self.results[post_id] = {"variants": {}, "total_views": 0}
        if variant not in self.results[post_id]["variants"]:
            self.results[post_id]["variants"][variant] = {"views": 0, "clicks": 0}
        self.results[post_id]["variants"][variant]["views"] += views
        self.results[post_id]["variants"][variant]["clicks"] += clicks
        self.results[post_id]["total_views"] += views

    def get_winner(self, post_id: str) -> Optional[str]:
        """Determine winning variant by click-through rate."""
        data = self.results.get(post_id)
        if not data or len(data["variants"]) < 2:
            return None

        best_variant = None
        best_ctr = -1.0
        for variant, stats in data["variants"].items():
            if stats["views"] > 0:
                ctr = stats["clicks"] / stats["views"]
                if ctr > best_ctr:
                    best_ctr = ctr
                    best_variant = variant
        return best_variant
