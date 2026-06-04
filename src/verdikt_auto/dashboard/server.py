"""Dashboard — aiohttp server with Chart.js analytics, Basic Auth."""

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import aiohttp.web
from aiohttp import web

from verdikt_auto.core.config import Settings
from verdikt_auto.core.models import ChannelStats

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency at module level
_metrics_collector: Optional[Any] = None


def _require_auth(settings: Settings):
    """Basic Auth decorator factory."""
    username = settings.dashboard_username or "admin"
    password = settings.dashboard_password or "admin"

    def decorator(handler):
        async def wrapper(request):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Basic "):
                return web.Response(
                    status=401,
                    headers={"WWW-Authenticate": 'Basic realm="VERDIKT Dashboard"'},
                    body="Unauthorized",
                )
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, pw = decoded.split(":", 1)
            except Exception:
                return web.Response(status=401, body="Unauthorized")
            if user != username or pw != password:
                return web.Response(status=401, body="Unauthorized")
            return await handler(request)
        return wrapper
    return decorator


class DashboardServer:
    """aiohttp dashboard with Chart.js, metrics, and post analytics."""

    def __init__(
        self,
        settings: Settings,
        host: str = "127.0.0.1",
        port: int = 5000,
        metrics_collector: Optional[Any] = None,
    ) -> None:
        self.settings = settings
        self.host = host
        self.port = port
        self.app = web.Application()
        self._stats: dict[str, Any] = {}
        self._post_stats: list[dict[str, Any]] = []
        self._daily_stats: list[dict[str, Any]] = []
        self._metrics_collector = metrics_collector
        self._setup_routes()

    def _setup_routes(self) -> None:
        auth = _require_auth(self.settings)
        self.app.router.add_get("/", auth(self._index))
        self.app.router.add_get("/metrics", auth(self._metrics))
        self.app.router.add_get("/posts", auth(self._posts))
        self.app.router.add_get("/settings", auth(self._settings_page))
        self.app.router.add_get("/api/stats", auth(self._api_stats))
        self.app.router.add_get("/api/health", self._health)

    def _ensure_data_loaded(self) -> None:
        if not self._stats and self._metrics_collector is not None:
            try:
                stats = self._metrics_collector.get_channel_stats()
                self._stats = stats.model_dump() if hasattr(stats, "model_dump") else dict(stats)
                daily = self._metrics_collector.tracker.get_daily(7)
                self._daily_stats = daily
            except Exception as exc:
                logger.warning("Failed to load analytics data: %s", exc)

    def update_stats(self, stats: ChannelStats) -> None:
        self._stats = stats.model_dump() if hasattr(stats, "model_dump") else dict(stats)

    def update_post_stats(self, posts: list[dict[str, Any]]) -> None:
        self._post_stats = posts

    def update_daily_stats(self, daily: list[dict[str, Any]]) -> None:
        self._daily_stats = daily

    async def _index(self, request: web.Request) -> web.Response:
        self._ensure_data_loaded()
        html = self._render_page("VERDIKT Dashboard", self._render_main())
        return web.Response(text=html, content_type="text/html")

    async def _metrics(self, request: web.Request) -> web.Response:
        self._ensure_data_loaded()
        html = self._render_page("Metrics — VERDIKT", self._render_metrics())
        return web.Response(text=html, content_type="text/html")

    async def _posts(self, request: web.Request) -> web.Response:
        html = self._render_page("Posts — VERDIKT", self._render_posts())
        return web.Response(text=html, content_type="text/html")

    async def _settings_page(self, request: web.Request) -> web.Response:
        html = self._render_page("Settings — VERDIKT", self._render_settings())
        return web.Response(text=html, content_type="text/html")

    async def _api_stats(self, request: web.Request) -> web.Response:
        return web.json_response(self._stats)

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": "0.1.0", "uptime": "N/A"})

    def _render_page(self, title: str, content: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; }}
  .nav {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px; }}
  .nav a {{ color: #58a6ff; text-decoration: none; margin-right: 20px; font-size: 14px; }}
  .nav a:hover {{ color: #79c0ff; }}
  .container {{ max-width: 1100px; margin: 24px auto; padding: 0 20px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin: 16px 0; }}
  .card h2 {{ color: #f0f6fc; font-size: 18px; margin-bottom: 12px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
  .stat {{ background: #0d1117; border-radius: 6px; padding: 16px; text-align: center; }}
  .stat .value {{ font-size: 28px; font-weight: bold; color: #58a6ff; }}
  .stat .label {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
  .chart-container {{ position: relative; height: 300px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #30363d; font-size: 13px; }}
  th {{ color: #8b949e; font-weight: 600; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; }}
  .badge-green {{ background: #1b3322; color: #3fb950; }}
  .badge-red {{ background: #3d1f1f; color: #f85149; }}
  .input {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 8px; color: #c9d1d9; width: 100%; }}
</style>
</head>
<body>
<div class="nav">
  <a href="/">🏠 Dashboard</a>
  <a href="/metrics">📊 Metrics</a>
  <a href="/posts">📝 Posts</a>
  <a href="/settings">⚙ Settings</a>
</div>
<div class="container">
{content}
</div>
</body>
</html>"""

    def _render_main(self) -> str:
        posts_count = self._stats.get("posts_count", 0)
        views = self._stats.get("views_last_7d", 0)
        engagement = self._stats.get("avg_engagement", 0.0)
        subscribers = self._stats.get("subscribers_count", 0)
        daily_labels = json.dumps([d.get("date", "") for d in self._daily_stats])
        daily_views = json.dumps([d.get("views", 0) for d in self._daily_stats])
        daily_reactions = json.dumps([d.get("reactions", 0) for d in self._daily_stats])

        return f"""
<div class="grid">
  <div class="stat"><div class="value">{posts_count}</div><div class="label">Posts</div></div>
  <div class="stat"><div class="value">{views}</div><div class="label">Views (7d)</div></div>
  <div class="stat"><div class="value">{engagement}%</div><div class="label">Engagement</div></div>
  <div class="stat"><div class="value">{subscribers}</div><div class="label">Subscribers</div></div>
</div>
<div class="card">
  <h2>Daily Views (7 days)</h2>
  <div class="chart-container">
    <canvas id="dailyChart"></canvas>
  </div>
</div>
<script>
new Chart(document.getElementById('dailyChart'), {{
  type: 'line',
  data: {{
    labels: {daily_labels},
    datasets: [
      {{ label: 'Views', data: {daily_views}, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.1)', fill: true }},
      {{ label: 'Reactions', data: {daily_reactions}, borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,0.1)', fill: true }}
    ]
  }},
  options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ labels: {{ color: '#c9d1d9' }} }} }},
    scales: {{ x: {{ ticks: {{ color: '#8b949e' }} }}, y: {{ ticks: {{ color: '#8b949e' }} }} }}
  }}
}});
</script>"""

    def _render_metrics(self) -> str:
        top_posts = self._stats.get("top_posts", [])
        top_html = ""
        for i, pid in enumerate(top_posts[:10], 1):
            top_html += f"<tr><td>{i}</td><td>{pid[:16]}...</td></tr>"
        return f"""
<div class="card">
  <h2>Channel Metrics</h2>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Posts</td><td>{self._stats.get('posts_count', 0)}</td></tr>
    <tr><td>Views (7d)</td><td>{self._stats.get('views_last_7d', 0)}</td></tr>
    <tr><td>Avg Engagement</td><td>{self._stats.get('avg_engagement', 0.0)}%</td></tr>
    <tr><td>Subscribers</td><td>{self._stats.get('subscribers_count', 0)}</td></tr>
  </table>
</div>
<div class="card">
  <h2>Top Posts</h2>
  <table><tr><th>#</th><th>Post ID</th></tr>{top_html}</table>
</div>"""

    def _render_posts(self) -> str:
        rows = ""
        for p in self._post_stats[-20:]:
            status_badge = '<span class="badge badge-green">Published</span>' if p.get("status") == "published" else '<span class="badge badge-red">Draft</span>'
            rows += f"<tr><td>{p.get('id', '')[:12]}</td><td>{p.get('topic', '')[:40]}</td><td>{p.get('category', '')}</td><td>{status_badge}</td></tr>"
        return f"""
<div class="card">
  <h2>Recent Posts</h2>
  <table>
    <tr><th>ID</th><th>Topic</th><th>Category</th><th>Status</th></tr>
    {rows or '<tr><td colspan="4">No posts yet</td></tr>'}
  </table>
</div>"""

    def _render_settings(self) -> str:
        return """
<div class="card">
  <h2>Settings</h2>
  <p>Configuration is managed via environment variables and <code>.env</code> file.</p>
  <table>
    <tr><th>Setting</th><th>Current Value</th></tr>
    <tr><td>Dashboard Port</td><td>5000</td></tr>
    <tr><td>Pipeline Interval</td><td>Every 4 hours</td></tr>
    <tr><td>Max Posts/Day</td><td>7</td></tr>
    <tr><td>Auto Commenting</td><td>20/day max</td></tr>
  </table>
</div>"""

    def start(self) -> None:
        logger.info("Dashboard starting at http://%s:%d", self.host, self.port)
        web.run_app(self.app, host=self.host, port=self.port)

    async def start_async(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info("Dashboard async started at http://%s:%d", self.host, self.port)
