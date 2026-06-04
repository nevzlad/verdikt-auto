"""TTS generator — creates voiceovers via AI Router + ffmpeg conversion."""

import asyncio
import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import GeneratedContent

logger = logging.getLogger(__name__)

TARGET_DURATION_SEC = 40
TARGET_WORDS = 130


class TTSGenerator:
    """Generate voice overs using AI Router with Edge TTS as primary."""

    CACHE_DIR = Path("data/tts_cache")

    def __init__(self, router: AIRouter, voice: str = "ru-RU-DmitryNeural") -> None:
        self.router = router
        self.voice = voice
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._ffmpeg_available = self._check_ffmpeg()

    def _check_ffmpeg(self) -> bool:
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=False)
            return True
        except FileNotFoundError:
            logger.warning("ffmpeg not found — OGG conversion disabled")
            return False

    def _check_edge_tts(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    async def generate_ogg(self, text: str, filename: Optional[str] = None) -> Optional[str]:
        mp3_path = await self._generate_mp3(text, filename)
        if mp3_path is None:
            return None
        if not self._ffmpeg_available:
            return mp3_path
        ogg_path = mp3_path.replace(".mp3", ".ogg")
        if Path(ogg_path).exists():
            return ogg_path
        return await self._convert_to_ogg(mp3_path, ogg_path)

    async def _generate_mp3(self, text: str, filename: Optional[str] = None) -> Optional[str]:
        if not text.strip():
            logger.warning("Empty text for TTS")
            return None

        text = self._adapt_for_tts(text)

        if filename is None:
            h = hashlib.md5(text.encode()).hexdigest()[:12]
            filename = f"tts_{h}"

        mp3_path = str(self.CACHE_DIR / f"{filename}.mp3")
        if Path(mp3_path).exists():
            logger.info("TTS cache HIT: %s", mp3_path)
            return mp3_path

        result = await self.router.route(
            "generate_tts",
            text=text,
            voice=self.voice,
            filename=mp3_path,
        )

        if isinstance(result, GeneratedContent):
            text = result.text

        if Path(mp3_path).exists():
            logger.info("TTS saved: %s", mp3_path)
            return mp3_path

        if self._check_edge_tts():
            return await self._edge_tts_fallback(text, filename, mp3_path)

        logger.warning("TTS generation returned no audio file")
        return None

    async def _edge_tts_fallback(self, text: str, filename: str, mp3_path: str) -> Optional[str]:
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text[:5000], voice=self.voice)
            await communicate.save(mp3_path)
            logger.info("Edge TTS fallback saved: %s", mp3_path)
            return mp3_path
        except Exception as exc:
            logger.error("Edge TTS fallback failed: %s", exc)
            return None

    async def _convert_to_ogg(self, mp3_path: str, ogg_path: str) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", mp3_path, "-c:a", "libopus",
                "-b:a", "24k", "-application", "voip",
                ogg_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            rc = await proc.wait()
            if rc == 0:
                logger.info("OGG converted: %s", ogg_path)
                return ogg_path
            logger.warning("ffmpeg exit code %d", rc)
            return mp3_path
        except Exception as exc:
            logger.error("ffmpeg conversion failed: %s", exc)
            return mp3_path

    def _adapt_for_tts(self, text: str) -> str:
        import re
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"#\w+", "", text)
        text = re.sub(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
                       r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        words = text.split()
        if len(words) > TARGET_WORDS:
            text = " ".join(words[:TARGET_WORDS])
        return text
