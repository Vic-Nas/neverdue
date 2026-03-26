# llm/extractor.py
import json
import anthropic
from django.conf import settings

client = anthropic.Anthropic(api_key=settings.LLM_API_KEY)

SYSTEM_PROMPT = """You are a calendar event extractor. Given text content from an email or document, extract all calendar events, deadlines, and scheduled items.

Return ONLY a valid JSON array. No explanation, no markdown, no extra text.

Each event must have:
- "title": concise event name (string)
- "description": relevant context from the source (string, can be empty)
- "start": ISO 8601 datetime string (e.g. "2025-09-15T09:00:00")
- "end": ISO 8601 datetime string (must be after start)
- "category_hint": suggested category name based on context (string, can be empty)
- "recurrence_freq": recurrence frequency if clearly stated — one of "DAILY", "WEEKLY", "MONTHLY", "YEARLY" or empty string if not recurring or uncertain
- "recurrence_until": end date for recurrence as "YYYY-MM-DD" string, or empty string if open-ended or not applicable

Rules:
- If only a date is given with no time, set start to 09:00 and end to 10:00 on that date
- If a deadline is mentioned with no end time, set end to 1 hour after start
- If no year is specified, assume the nearest future occurrence
- If no events are found, return an empty array []
- Never return null values — use empty strings instead
- All datetimes must be in UTC
- Only set recurrence_freq if you are highly confident — when in doubt leave it empty
- Never set recurrence_freq if the event duration would equal or exceed the recurrence interval (e.g. a month-long event cannot be weekly)

Example output:
[
  {
    "title": "Submit thesis draft",
    "description": "Final draft due to supervisor",
    "start": "2025-11-01T09:00:00",
    "end": "2025-11-01T10:00:00",
    "category_hint": "University",
    "recurrence_freq": "",
    "recurrence_until": ""
  },
  {
    "title": "Weekly lecture",
    "description": "COMP 101 every Monday",
    "start": "2025-09-08T10:00:00",
    "end": "2025-09-08T11:00:00",
    "category_hint": "Courses",
    "recurrence_freq": "WEEKLY",
    "recurrence_until": "2025-12-15"
  }
]"""


def extract_events(text: str, language: str = 'English') -> list[dict]:
    """
    Extract calendar events from plain text.
    Returns a list of validated event dicts.
    Raises ValueError if LLM returns invalid output.
    """
    message = client.messages.create(
        model=settings.LLM_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, and category hints must be in {language}.',
        messages=[
            {'role': 'user', 'content': f'Extract all calendar events from this content:\n\n{text}'}
        ]
    )

    raw = message.content[0].text.strip()

    # Strip accidental markdown fences
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
        raw = raw.strip()

    try:
        events = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f'LLM returned invalid JSON: {e}\nRaw output: {raw}')

    if not isinstance(events, list):
        raise ValueError(f'LLM returned non-list: {type(events)}')

    return [_validate_event(e) for e in events if _validate_event(e)]


def extract_events_from_image(file_bytes: bytes, media_type: str, context: str = '', language: str = 'English') -> list[dict]:
    """
    Extract calendar events from an image or PDF.
    media_type: 'image/jpeg', 'image/png', or 'application/pdf'
    """
    import base64
    encoded = base64.standard_b64encode(file_bytes).decode('utf-8')

    if media_type == 'application/pdf':
        source = {
            'type': 'base64',
            'media_type': media_type,
            'data': encoded,
        }
        content_block = {'type': 'document', 'source': source}
    else:
        source = {
            'type': 'base64',
            'media_type': media_type,
            'data': encoded,
        }
        content_block = {'type': 'image', 'source': source}

    user_text = 'Extract all calendar events from this file.'
    if context:
        user_text += f'\n\nUser context: {context}'

    message = client.messages.create(
        model=settings.LLM_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, and category hints must be in {language}.',
        messages=[
            {
                'role': 'user',
                'content': [
                    content_block,
                    {'type': 'text', 'text': user_text}
                ]
            }
        ]
    )

    raw = message.content[0].text.strip()

    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
        raw = raw.strip()

    try:
        events = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f'LLM returned invalid JSON: {e}\nRaw output: {raw}')

    if not isinstance(events, list):
        raise ValueError(f'LLM returned non-list: {type(events)}')

    return [_validate_event(e) for e in events if _validate_event(e)]


VALID_FREQS = {'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'}

RECURRENCE_MIN_INTERVAL_DAYS = {
    'DAILY': 1,
    'WEEKLY': 7,
    'MONTHLY': 30,
    'YEARLY': 365,
}


def _validate_event(event: dict) -> dict | None:
    """
    Validate and clean a single event dict.
    Returns None if the event is missing required fields.
    """
    from datetime import datetime

    required = ('title', 'start', 'end')
    for field in required:
        if not event.get(field):
            return None

    # Validate recurrence against event duration
    recurrence_freq = event.get('recurrence_freq', '').strip().upper()
    if recurrence_freq not in VALID_FREQS:
        recurrence_freq = ''

    if recurrence_freq:
        try:
            start = datetime.fromisoformat(event['start'])
            end = datetime.fromisoformat(event['end'])
            duration_days = (end - start).total_seconds() / 86400
            min_days = RECURRENCE_MIN_INTERVAL_DAYS[recurrence_freq]
            if duration_days >= min_days:
                recurrence_freq = ''  # block invalid recurrence silently
        except (ValueError, TypeError):
            recurrence_freq = ''

    recurrence_until = event.get('recurrence_until', '').strip() if recurrence_freq else ''

    return {
        'title': str(event.get('title', '')).strip()[:255],
        'description': str(event.get('description', '')).strip(),
        'start': event['start'],
        'end': event['end'],
        'category_hint': str(event.get('category_hint', '')).strip(),
        'recurrence_freq': recurrence_freq,
        'recurrence_until': recurrence_until,
    }