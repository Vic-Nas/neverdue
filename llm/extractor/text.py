# llm/extractor/text.py
import logging

from django.conf import settings

from .client import call_api
from .prompts import SYSTEM_PROMPT
from .utils import get_tz, today_in_tz
from .validation import parse_and_validate

logger = logging.getLogger(__name__)


def extract_events(
    text: str,
    language: str = 'English',
    user_timezone: str = 'UTC',
    user_instructions: str = '',
    existing_categories: list[str] | None = None,
) -> tuple[list[dict], int, int]:
    tz = get_tz(user_timezone)
    today = today_in_tz(tz)
    system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    user_content = f"Today's date: {today}\nUser's timezone: {user_timezone}\n"
    if existing_categories:
        cats = ', '.join(existing_categories)
        user_content += (
            f"\nExisting categories (prefer these for category_hint when they fit, "
            f"only invent a new one if none are appropriate): {cats}\n"
        )
    if user_instructions:
        user_content += f"\nUser instructions (follow strictly):\n{user_instructions}\n"
    user_content += f"\nExtract all calendar events from this content:\n\n{text}"

    message = call_api(
        model=settings.LLM_MODEL,
        max_tokens=2000,
        system=system,
        messages=[{'role': 'user', 'content': user_content}]
    )

    events = parse_and_validate(message, tz)
    return events, message.usage.input_tokens, message.usage.output_tokens