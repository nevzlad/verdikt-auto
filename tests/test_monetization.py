"""Tests for monetization modules."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from verdikt_auto.monetization.ads import AdsManager, AffiliateTracker, PremiumGate


class TestAdsManager:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"ads": [], "impressions": {}}')
        self.tmp.close()
        self.mgr = AdsManager(db_path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_register_and_select(self) -> None:
        self.mgr.register("ad_1", "Купите наш курс!", weight=1.0)
        ad = self.mgr.select()
        assert ad == "Купите наш курс!"

    def test_select_fallback(self) -> None:
        assert self.mgr.select() is None

    def test_insert_end(self) -> None:
        result = self.mgr.insert("Пост текст", "Реклама")
        assert result.endswith("Реклама")

    def test_insert_start(self) -> None:
        result = self.mgr.insert("Пост текст", "Реклама", position="start")
        assert result.startswith("Реклама")

    def test_insert_middle(self) -> None:
        result = self.mgr.insert("Строка1\nСтрока2", "Реклама", position="middle")
        assert "Реклама" in result

    def test_stats(self) -> None:
        self.mgr.register("ad_1", "Текст")
        self.mgr.select()
        stats = self.mgr.stats()
        assert stats["total_ads"] == 1
        assert stats["total_impressions"] >= 1


class TestAffiliateTracker:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"links": {}, "clicks": {}, "conversions": {}}')
        self.tmp.close()
        self.at = AffiliateTracker(path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_register_and_enrich(self) -> None:
        self.at.register("курс", "https://example.com/course")
        text = "Лучший курс по Python"
        enriched = self.at.enrich(text)
        assert "https://example.com/course" in enriched

    def test_track_click(self) -> None:
        self.at.track_click("link_1")
        assert sum(self.at._clicks.values()) == 1

    def test_track_conversion(self) -> None:
        self.at.register("link_1", "https://example.com")
        self.at.track_click("link_1")
        self.at.track_conversion("link_1")
        report = self.at.report()
        assert any(r["clicks"] == 1 and r["conversions"] == 1 for r in report)

    def test_report_empty(self) -> None:
        assert self.at.report() == []


class TestPremiumGate:
    def setup_method(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.tmp.write('{"subscribers": {}, "exclusive": []}')
        self.tmp.close()
        self.pg = PremiumGate(path=self.tmp.name)

    def teardown_method(self) -> None:
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_subscribe_and_check(self) -> None:
        self.pg.subscribe("user_1")
        assert self.pg.check("user_1") is True

    def test_unsubscribe(self) -> None:
        self.pg.subscribe("user_1")
        self.pg.unsubscribe("user_1")
        assert self.pg.check("user_1") is False

    def test_check_nonexistent(self) -> None:
        assert self.pg.check("ghost") is False

    def test_add_exclusive(self) -> None:
        self.pg.subscribe("user_1")
        self.pg.add_exclusive("post_1", "Тема", "Текст")
        content = self.pg.get_exclusive("user_1", 10)
        assert len(content) == 1
        assert content[0]["title"] == "Тема"

    def test_exclusive_denied(self) -> None:
        self.pg.add_exclusive("post_1", "Тема", "Текст")
        content = self.pg.get_exclusive("non_premium", 10)
        assert content == []

    def test_count(self) -> None:
        self.pg.subscribe("user_1")
        self.pg.subscribe("user_2")
        assert self.pg.count() == 2
