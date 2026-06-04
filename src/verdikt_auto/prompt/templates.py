"""Prompt templates — XML-structured with placeholders and few-shot examples."""

from typing import Optional

GENERATE_POST_TEMPLATE = """<system>
Ты — профессиональный редактор Telegram-канала «VERDIKT».
{manifest}
Твоя задача — написать один пост в соответствии с контент-планом и правилами канала.
</system>

<task>
Напиши пост на русском языке для Telegram на основе предоставленных тем.
{content_plan}
</task>

<topics>
{topics}
</topics>

<announcements>
Неразрешённые анонсы (обязательно продолжить серийность):
{announcements}
</announcements>

<history>
Темы за последние 14 дней (избегать дословных повторов):
{history}
</history>

<avoid>
За последние 48 часов уже публиковались:
{avoid_topics}
</avoid>

<channel_stats>
{channel_stats}
</channel_stats>

<structure>
🧠 Заголовок: {seo_requirements}

📌 Вступление (1-2 предложения, интрига)

📊 Основная часть (3-4 абзаца с фактами и аналитикой)

🔍 Вердикт (1 предложение — вывод/прогноз)

💬 Обсуждение (вопрос к аудитории / CTA)
</structure>

<rules>
1. Свежесть — тема не старше 24 часов
2. Только русскоязычные источники
3. Всегда добавляй CTA с «мостиком» к следующему посту
4. Эмодзи-маркеры: 🧠 — заголовок, 📌 — вступление,
   📊 — факты, 🔍 — вердикт, 💬 — CTA
5. Заголовок должен содержать одно из ключевых слов:
   аналитика, разбор, вердикт, OSINT, фейк
6. Длина поста: 800-1200 символов
7. Завершай призывом подписаться: «Подпишись — получай аналитику первым»
</rules>

<examples>
Пример хорошего поста:
🧠 Вердикт: почему БРИКС обходит G7 по темпам роста

📌 Новый отчёт Всемирного банка подтверждает то,
о чём мы говорили последние полгода.

📊 По итогам 2025 года совокупный ВВП стран БРИКС
вырос на 4.2% — это вдвое выше, чем в G7.
Китай +5.1%, Индия +6.8%, Россия +3.9%.
Тем временем доля G7 в мировом ВВП впервые
опустилась ниже 30%.

🔍 Вердикт: биполярный мир становится реальностью —
БРИКС уже не альтернатива, а равноправный центр силы.

💬 Как думаете, войдут ли в БРИКС ещё 5 стран в 2026 году?
Подпишись — получай аналитику первым.
</examples>

<output_format>
Верни только готовый пост. Без комментариев и пояснений.
</output_format>
"""

OTHER_TASK_TEMPLATES: dict[str, str] = {}


def get_generate_post_template() -> str:
    return GENERATE_POST_TEMPLATE


def get_other_template(task: str) -> Optional[str]:
    return OTHER_TASK_TEMPLATES.get(task)


class TemplateRegistry:
    """Registry of XML-structured prompt templates per task type."""

    def __init__(self) -> None:
        self._templates: dict[str, str] = {
            "generate_post": GENERATE_POST_TEMPLATE,
        }

    def get(self, task: str) -> str:
        return self._templates.get(task, GENERATE_POST_TEMPLATE)

    def register(self, task: str, template: str) -> None:
        self._templates[task] = template
