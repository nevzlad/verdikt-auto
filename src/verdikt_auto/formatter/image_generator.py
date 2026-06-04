"""Image generator — creates post visuals via AI Router + Pillow overlays."""

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import GeneratedContent, PostCategory

logger = logging.getLogger(__name__)

CATEGORY_TEMPLATES: dict[PostCategory, str] = {
    PostCategory.SVO: "Военная хроника, реалистичный стиль, новостная фотография",
    PostCategory.MIDEAST: "Ближневосточный конфликт, реалистичный стиль, новостная фотография",
    PostCategory.USA: "Американская политика, реалистичный стиль, фоторепортаж",
    PostCategory.ECONOMY: "Деловая графика, инфографика, диаграммы, минимализм",
    PostCategory.TECH: "Футуристический стиль, киберпанк, неон, технологии",
    PostCategory.GENERAL: "Нейтральный новостной стиль, фотография высокого качества",
}

FALLBACK_BG_COLORS: dict[PostCategory, tuple[int, int, int]] = {
    PostCategory.SVO: (45, 45, 60),
    PostCategory.MIDEAST: (60, 45, 30),
    PostCategory.USA: (30, 45, 60),
    PostCategory.ECONOMY: (30, 55, 40),
    PostCategory.TECH: (20, 25, 50),
    PostCategory.GENERAL: (40, 40, 40),
}


class ImageGenerator:
    """Generates, post-processes and caches post images."""

    IMAGE_SIZE = (1080, 1350)
    CACHE_DIR = Path("data/image_cache")

    def __init__(self, router: AIRouter) -> None:
        self.router = router
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._pillow_available = self._check_pillow()

    def _check_pillow(self) -> bool:
        try:
            from PIL import Image, ImageDraw, ImageFont  # noqa: F401
            return True
        except ImportError:
            logger.warning("Pillow not installed — image overlays disabled")
            return False

    async def generate_image(self, prompt: str, category: PostCategory = PostCategory.GENERAL) -> Optional[str]:
        cache_path = self._cache_path(prompt)
        if cache_path.exists():
            logger.info("Image cache HIT: %s", cache_path)
            return str(cache_path)

        raw = await self.router.route("generate_image", prompt=prompt)
        image_data = self._extract_image(raw)
        if image_data is None:
            logger.warning("All image APIs failed, using local fallback")
            return await self._generate_fallback(prompt, category)

        result_path = await self._apply_overlays(image_data, prompt, category)
        if result_path:
            return str(result_path)
        return None

    def _extract_image(self, raw: object) -> Optional[bytes]:
        if isinstance(raw, GeneratedContent):
            raw = raw.text
        if isinstance(raw, str):
            if os.path.isfile(raw):
                with open(raw, "rb") as f:
                    return f.read()
            return None
        if isinstance(raw, bytes):
            return raw
        return None

    async def _apply_overlays(self, image_data: bytes, prompt: str, category: PostCategory) -> Optional[Path]:
        if not self._pillow_available:
            cache_path = self._cache_path(prompt)
            cache_path.write_bytes(image_data)
            return cache_path

        try:
            from PIL import Image, ImageDraw, ImageFont

            img = Image.open(BytesIO(image_data)).convert("RGB").resize(self.IMAGE_SIZE, Image.LANCZOS)
            draw = ImageDraw.Draw(img)

            title = self._extract_title(prompt)
            logo_path = self._find_logo()

            font_title = self._load_font(48)
            font_category = self._load_font(24)

            if font_title and title:
                self._draw_text_with_shadow(draw, title, font_title, 60, 100, (255, 255, 255))

            if font_category:
                badge_text = category.value.upper()
                self._draw_category_badge(draw, badge_text, font_category)

            if logo_path:
                self._draw_logo(img, logo_path)

            cache_path = self._cache_path(prompt)
            img.save(cache_path, "JPEG", quality=85)
            logger.info("Image with overlays saved: %s", cache_path)
            return cache_path
        except Exception as exc:
            logger.error("Pillow overlay failed: %s", exc)
            cache_path = self._cache_path(prompt)
            cache_path.write_bytes(image_data)
            return cache_path

    def _extract_title(self, prompt: str) -> str:
        lines = prompt.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and len(line) > 10 and not line.startswith(("http", "#", "@")):
                return line[:80]
        return ""

    def _find_logo(self) -> Optional[Path]:
        possible = [Path("data/logo.png"), Path("assets/logo.png"), Path("logo.png")]
        for p in possible:
            if p.exists():
                return p
        return None

    def _load_font(self, size: int) -> object:
        try:
            from PIL import ImageFont
            for name in ["BebasNeue-Regular.ttf", "BebasNeue.otf"]:
                font_path = Path(f"fonts/{name}")
                if font_path.exists():
                    return ImageFont.truetype(str(font_path), size)
            return ImageFont.load_default()
        except ImportError:
            return None

    def _draw_text_with_shadow(self, draw: object, text: str, font: object, x: int, y: int, fill: tuple) -> None:
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_category_badge(self, draw: object, text: str, font: object) -> None:
        from PIL import ImageDraw
        bbox = draw.textbbox((0, 0), text, font=font)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bx, by = 60, self.IMAGE_SIZE[1] - 80
        draw.rounded_rectangle((bx, by, bx + bw + 40, by + bh + 20), radius=12, fill=(200, 32, 32, 200))
        draw.text((bx + 20, by + 10), text, font=font, fill=(255, 255, 255))

    def _draw_logo(self, img: object, logo_path: Path) -> None:
        from PIL import Image
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((120, 120), Image.LANCZOS)
        img.paste(logo, (self.IMAGE_SIZE[0] - 160, 40), logo if logo.mode == "RGBA" else None)

    async def _generate_fallback(self, prompt: str, category: PostCategory) -> Optional[str]:
        if not self._pillow_available:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont

            bg = FALLBACK_BG_COLORS.get(category, (40, 40, 40))
            img = Image.new("RGB", self.IMAGE_SIZE, bg)
            draw = ImageDraw.Draw(img)
            font = self._load_font(36)

            lines = self._wrap_text(prompt, 35)
            y = self.IMAGE_SIZE[1] // 2 - len(lines) * 20
            for line in lines[:10]:
                draw.text((60, y), line, font=font, fill=(220, 220, 220))
                y += 45

            cache_path = self._cache_path(prompt + ":fallback")
            img.save(cache_path, "JPEG", quality=80)
            logger.info("Fallback image saved: %s", cache_path)
            return str(cache_path)
        except Exception as exc:
            logger.error("Fallback image failed: %s", exc)
            return None

    def _wrap_text(self, text: str, max_chars: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > max_chars:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines

    def _cache_path(self, prompt: str) -> Path:
        h = hashlib.md5(prompt.encode()).hexdigest()
        return self.CACHE_DIR / f"img_{h}.jpg"


try:
    from io import BytesIO
except ImportError:
    BytesIO = None
