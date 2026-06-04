"""Sources manager — loads, validates, and manages scan sources."""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from verdikt_auto.scanner.models import SourceConfig

logger = logging.getLogger(__name__)


class SourcesManager:
    """Manages scan sources from YAML config with dynamic add/remove."""

    def __init__(self, yaml_path: str = "sources.yaml") -> None:
        self.yaml_path = yaml_path
        self._sources: dict[str, list[SourceConfig]] = {
            "youtube": [],
            "telegram": [],
            "rss": [],
        }

    def load(self) -> None:
        """Load sources from YAML."""
        path = Path(self.yaml_path)
        if not path.exists():
            logger.warning("Sources file not found: %s", self.yaml_path)
            return

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for source_type in ("youtube", "telegram", "rss"):
            raw_list = data.get(source_type, {}).get(
                "channels" if source_type in ("youtube", "telegram") else "feeds",
                [],
            )
            self._sources[source_type] = []

            for item in raw_list:
                if source_type == "youtube":
                    config = SourceConfig(
                        source_type="youtube",
                        name=item.get("name", item.get("id", "")),
                        identifier=item.get("id", ""),
                        keywords=item.get("keywords", []),
                    )
                elif source_type == "telegram":
                    config = SourceConfig(
                        source_type="telegram",
                        name=item.strip("@") if isinstance(item, str) else item.get("name", ""),
                        identifier=item.strip("@") if isinstance(item, str) else item.get("id", ""),
                    )
                else:  # rss
                    config = SourceConfig(
                        source_type="rss",
                        name=item.get("url", ""),
                        identifier=item.get("url", ""),
                        keywords=[item.get("category", "general")],
                    )

                self._sources[source_type].append(config)

        logger.info(
            "Loaded sources: %d youtube, %d telegram, %d rss",
            len(self._sources["youtube"]),
            len(self._sources["telegram"]),
            len(self._sources["rss"]),
        )

    def add_source(self, source_type: str, config: SourceConfig) -> None:
        if source_type not in self._sources:
            raise ValueError(f"Unknown source type: {source_type}")
        self._sources[source_type].append(config)
        logger.info("Added %s source: %s", source_type, config.name)

    def remove_source(self, source_type: str, identifier: str) -> bool:
        if source_type not in self._sources:
            return False
        before = len(self._sources[source_type])
        self._sources[source_type] = [
            s for s in self._sources[source_type] if s.identifier != identifier
        ]
        removed = before - len(self._sources[source_type])
        if removed:
            logger.info("Removed %s source: %s", source_type, identifier)
        return removed > 0

    def get_youtube_channels(self) -> list[dict]:
        return [
            {"id": s.identifier, "name": s.name, "keywords": s.keywords}
            for s in self._sources["youtube"]
        ]

    def get_telegram_channels(self) -> list[str]:
        return [s.identifier for s in self._sources["telegram"]]

    def get_rss_feeds(self) -> list[dict]:
        return [{"url": s.identifier, "category": s.keywords[0] if s.keywords else "general"} for s in self._sources["rss"]]

    def get_all_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._sources.items()}

    def validate_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for source_type, sources in self._sources.items():
            for s in sources:
                key = f"{source_type}:{s.identifier}"
                results[key] = bool(s.identifier)
        return results
