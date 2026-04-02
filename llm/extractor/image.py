import base64
import logging

from django.conf import settings

from .client import call_api
from .prompts import SYSTEM_PROMPT
from .utils import get_tz, today_in_tz
from .validation import parse_and_validate

logger = logging.getLogger(__name__)


def extract_events_from_image(
    file_bytes: bytes, media_type: str, context: str = '',
    language: str = 'English', user_timezone: str = 'UTC', user_instructions: str = '',
) -> tuple[list[dict], int, int]:
    encoded = base64.standard_b64encode(file_bytes).decode('utf-8')

    if media_type == 'application/pdf':
        content_block = {'type': 'document', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}}
    else:
        content_block = {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}}

    tz = get_tz(user_timezone)
    today = today_in_tz(tz)
    system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    user_text = f"Today's date: {today}\nUser's timezone: {user_timezone}\n\nExtract all calendar events from this file."
    if context:
        user_text += f'\n\nUser context (follow strictly): {context}'
    if user_instructions:
        user_text += f'\n\nUser instructions (follow strictly): {user_instructions}'

    message = call_api(
        model=settings.LLM_MODEL,
        max_tokens=2000,
        system=system,
        messages=[{'role': 'user', 'content': [content_block, {'type': 'text', 'text': user_text}]}]
    )

    events = parse_and_validate(message, tz)
    return events, message.usage.input_tokens, message.usage.output_tokens
