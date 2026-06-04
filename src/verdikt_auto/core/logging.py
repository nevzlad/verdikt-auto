"""Logging configuration with Rich."""

import logging
import sys
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback


def setup_logging(level: str = "INFO") -> None:
    install_rich_traceback(show_locals=True)

    console = Console(stderr=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                markup=True,
                show_time=True,
                show_path=True,
            )
        ],
    )

    for lib in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)
