"""Application configuration via pydantic-settings + YAML."""

from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIConfig(BaseSettings):
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    cerebras_api_key: str = ""
    gemini_api_key: str = ""
    deepseek_api_key: str = ""
    replicate_api_key: str = ""


class ScannerConfig(BaseSettings):
    youtube_api_key: str = ""


class TelegramConfig(BaseSettings):
    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    admin_chat_id: str = ""


class YamlConfig:
    def __init__(self, path: str = "config.yaml") -> None:
        self._data: dict[str, Any] = {}
        config_path = Path(path)
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Flattened from .env
    log_level: str = "INFO"

    # Sub-configs
    ai: AIConfig = AIConfig()
    scanner: ScannerConfig = ScannerConfig()
    telegram: TelegramConfig = TelegramConfig()
    yaml_config: YamlConfig = YamlConfig()

    @property
    def posts_per_day(self) -> int:
        return self.yaml_config.get("posts_per_day", 7)

    @property
    def schedule_hours(self) -> list[int]:
        return self.yaml_config.get("schedule_hours", [7, 9, 12, 14, 18, 20, 22])

    @property
    def ai_routing(self) -> dict[str, Any]:
        return self.yaml_config.get("ai_routing", {})

    @property
    def topic_weights(self) -> dict[str, float]:
        return self.yaml_config.get("topic_weights", {})

    @property
    def image_config(self) -> dict[str, Any]:
        return self.yaml_config.get("image", {})

    @property
    def tts_config(self) -> dict[str, Any]:
        return self.yaml_config.get("tts", {})

    @property
    def scanner_config(self) -> dict[str, Any]:
        return self.yaml_config.get("scanner", {})

    @property
    def database_path(self) -> str:
        return self.yaml_config.get("database.path", "data/verdikt.db")
