"""Tests for dashboard server."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdikt_auto.core.models import ChannelStats


class TestDashboardServerAuth:
    def test_auth_wrapper_rejects_no_header(self) -> None:
        from verdikt_auto.dashboard.server import _require_auth
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        auth = _require_auth(settings)

        async def fake_handler(request):
            return "OK"

        wrapped = auth(fake_handler)
        request = MagicMock()
        request.headers = {}

        import asyncio
        resp = asyncio.run(wrapped(request))
        assert resp.status == 401

    def test_auth_wrapper_rejects_bad_password(self) -> None:
        from verdikt_auto.dashboard.server import _require_auth
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        auth = _require_auth(settings)

        async def fake_handler(request):
            return "OK"

        wrapped = auth(fake_handler)
        request = MagicMock()
        credentials = base64.b64encode(b"admin:wrong").decode()
        request.headers = {"Authorization": f"Basic {credentials}"}

        import asyncio
        resp = asyncio.run(wrapped(request))
        assert resp.status == 401

    def test_auth_wrapper_allows_valid(self) -> None:
        from verdikt_auto.dashboard.server import _require_auth
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        auth = _require_auth(settings)

        async def fake_handler(request):
            from aiohttp import web
            return web.Response(text="ok", content_type="text/html")

        wrapped = auth(fake_handler)
        request = MagicMock()
        credentials = base64.b64encode(b"admin:admin").decode()
        request.headers = {"Authorization": f"Basic {credentials}"}

        import asyncio
        resp = asyncio.run(wrapped(request))
        assert resp.status == 200
        assert resp.text == "ok"

    def test_health_endpoint(self) -> None:
        from verdikt_auto.dashboard.server import DashboardServer
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        ds = DashboardServer(settings, host="127.0.0.1", port=0)

        import asyncio
        resp = asyncio.run(ds._health(MagicMock()))
        assert resp.status == 200


class TestDashboardServerUnit:
    def test_update_stats(self) -> None:
        from verdikt_auto.dashboard.server import DashboardServer
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        app = DashboardServer(settings, host="127.0.0.1", port=0)
        stats = ChannelStats(posts_count=10, views_last_7d=500)
        app.update_stats(stats)
        assert app._stats["posts_count"] == 10

    def test_update_post_stats(self) -> None:
        from verdikt_auto.dashboard.server import DashboardServer
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        app = DashboardServer(settings, host="127.0.0.1", port=0)
        app.update_post_stats([{"id": "1", "topic": "Test"}])
        assert len(app._post_stats) == 1

    def test_update_daily_stats(self) -> None:
        from verdikt_auto.dashboard.server import DashboardServer
        settings = MagicMock()
        settings.dashboard_username = "admin"
        settings.dashboard_password = "admin"
        app = DashboardServer(settings, host="127.0.0.1", port=0)
        app.update_daily_stats([{"date": "2025-01-01", "views": 100}])
        assert len(app._daily_stats) == 1
