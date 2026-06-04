"""CLI entry point for VERDIKT-AUTO."""

import sys
from typing import NoReturn

from rich.console import Console

from verdikt_auto.core.config import Settings
from verdikt_auto.core.logging import setup_logging
from verdikt_auto.analytics.metrics import MetricsCollector
from verdikt_auto.publisher.scheduler import Scheduler
from verdikt_auto.dashboard.http_server import DashboardServer

console = Console()


def run_pipeline() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    scheduler = Scheduler(settings)
    scheduler.run_forever()


def run_dashboard() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    metrics = MetricsCollector(db_path=settings.database_path.replace(".db", "_metrics.db"))
    server = DashboardServer(settings, metrics_collector=metrics)
    server.start()


def main() -> NoReturn:
    if len(sys.argv) < 2:
        console.print("[bold red]Usage:[/bold red] verdikt-auto [pipeline|dashboard]")
        sys.exit(1)

    command = sys.argv[1]
    commands = {
        "pipeline": run_pipeline,
        "dashboard": run_dashboard,
    }

    if command not in commands:
        console.print(f"[bold red]Unknown command:[/bold red] {command}")
        console.print("Available: pipeline, dashboard")
        sys.exit(1)

    commands[command]()
    sys.exit(0)


if __name__ == "__main__":
    main()
