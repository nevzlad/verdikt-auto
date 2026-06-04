"""Repurpose Engine — adapts posts for Shorts/Dzen/Twitter."""

import logging
from typing import Any, Optional

from verdikt_auto.core.ai_router import AIRouter
from verdikt_auto.core.models import GeneratedContent, Post

logger = logging.getLogger(__name__)


class RepurposeEngine:
    """Converts a single post into platform-specific formats."""

    def __init__(self, router: AIRouter) -> None:
        self.router = router

    async def for_shorts(self, post: Post, duration_sec: int = 60) -> dict[str, Any]:
        prompt = (
            f"Адаптируй текст для YouTube Shorts / TikTok длительностью {duration_sec} сек.\n"
            f"Требования:\n"
            f"- Зацепка в первые 3 секунды\n"
            f"- Короткие рубленые фразы\n"
            f"- Призыв досмотреть до конца\n"
            f"- Максимум 500 символов\n\n"
            f"Тема: {post.topic}\n"
            f"Текст: {post.content.text[:2000]}"
        )
        result = await self.router.route("generate_post", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return {"platform": "shorts", "text": text[:1500], "source_id": post.id}

    async def for_dzen(self, post: Post) -> dict[str, Any]:
        prompt = (
            f"Адаптируй текст для Яндекс.Дзен.\n"
            f"Требования:\n"
            f"- Развёрнутый заголовок (до 120 символов)\n"
            f"- Введение с интригой\n"
            f"- 3-4 абзаца основного текста\n"
            f"- Вывод в конце\n"
            f"- Максимум 2500 символов\n\n"
            f"Тема: {post.topic}\n"
            f"Текст: {post.content.text[:2000]}"
        )
        result = await self.router.route("generate_post", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return {"platform": "dzen", "text": text[:2500], "source_id": post.id}

    async def for_twitter(self, post: Post) -> dict[str, Any]:
        prompt = (
            f"Адаптируй текст для X/Twitter (280 символов).\n"
            f"Требования:\n"
            f"- Максимум 280 символов\n"
            f"- Одна мощная мысль\n"
            f"- Можно добавить 1-2 хештега\n"
            f"- Призыв к действию (ретвит/комментарий)\n\n"
            f"Тема: {post.topic}\n"
            f"Текст: {post.content.text[:2000]}"
        )
        result = await self.router.route("generate_post", prompt=prompt)
        text = result.text if isinstance(result, GeneratedContent) else str(result)
        return {"platform": "twitter", "text": text[:280], "source_id": post.id}

    async def repurpose_all(self, post: Post) -> list[dict[str, Any]]:
        import asyncio
        tasks = [
            self.for_shorts(post),
            self.for_dzen(post),
            self.for_twitter(post),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = []
        for r in results:
            if isinstance(r, dict):
                valid.append(r)
            else:
                logger.warning("Repurpose failed: %s", r)
        return valid
