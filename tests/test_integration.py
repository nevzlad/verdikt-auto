"""Integration tests for analytics → dashboard data flow."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.analytics.metrics import MetricsCollector, PostPerformanceTracker


class TestAnalyticsDashboardFlow:
    """Verifies that analytics data flows correctly into the DashboardServer."""

    def setup_method(self) -> None:
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tracker = PostPerformanceTracker(db_path=self.db.name)
        self.collector = MetricsCollector(db_path=self.db.name)

    def teardown_method(self) -> None:
        try:
            self.tracker.close()
        except Exception:
            pass
        try:
            Path(self.db.name).unlink(missing_ok=True)
        except PermissionError:
            pass

    def test_dashboard_loads_data_from_analytics_db(self) -> None:
        """Dashboard with MetricsCollector reads analytics data on page load."""
        from verdikt_auto.dashboard.server import DashboardServer

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        self.tracker.record_daily(today, posts=3, views=500, reactions=25, new_subscribers=10)
        self.tracker.record_daily(yesterday, posts=4, views=700, reactions=30, new_subscribers=5)
        self.tracker.record("post_abc", "1h", views=200, reactions=10)

        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        server = DashboardServer(settings, host="127.0.0.1", port=0, metrics_collector=self.collector)

        server._ensure_data_loaded()

        assert server._stats["posts_count"] == 7
        assert server._stats["views_last_7d"] == 1200
        assert len(server._daily_stats) == 2
        assert server._stats["avg_engagement"] > 0

    def test_dashboard_empty_without_collector(self) -> None:
        """Dashboard without MetricsCollector shows empty stats."""
        from verdikt_auto.dashboard.server import DashboardServer

        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        server = DashboardServer(settings, host="127.0.0.1", port=0)

        server._ensure_data_loaded()

        assert server._stats == {}

    def test_dashboard_renders_with_analytics_data(self) -> None:
        """Dashboard HTML rendering includes analytics values."""
        from verdikt_auto.dashboard.server import DashboardServer

        today = datetime.now().strftime("%Y-%m-%d")
        self.tracker.record_daily(today, posts=5, views=1000, reactions=50, new_subscribers=20)

        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        server = DashboardServer(settings, host="127.0.0.1", port=0, metrics_collector=self.collector)

        import asyncio
        request = MagicMock()
        request.headers = {
            "Authorization": "Basic " + __import__("base64").b64encode(b"admin:admin").decode()
        }

        resp = asyncio.run(server._index(request))
        html = resp.text

        assert "5" in html
        assert "1000" in html
        assert "VERDIKT Dashboard" in html

    def test_update_stats_and_query(self) -> None:
        """Dashboard update_stats + update_daily_stats round-trips correctly."""
        from verdikt_auto.core.models import ChannelStats
        from verdikt_auto.dashboard.server import DashboardServer

        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        server = DashboardServer(settings, host="127.0.0.1", port=0, metrics_collector=self.collector)

        stats = ChannelStats(posts_count=15, views_last_7d=3500, avg_engagement=4.2, subscribers_count=1200)
        server.update_stats(stats)
        server.update_daily_stats([{"date": "2025-03-01", "views": 500, "reactions": 30}])

        assert server._stats["posts_count"] == 15
        assert server._stats["views_last_7d"] == 3500
        assert len(server._daily_stats) == 1
        assert server._daily_stats[0]["views"] == 500
