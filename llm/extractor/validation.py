# llm/extractor/validation.py
import json
import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings

logger = logging.getLogger(__name__)

VALID_FREQS = {'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'}
RECURRENCE_MIN_INTERVAL_DAYS = {
    'DAILY': 1, 'WEEKLY': 7, 'MONTHLY': 30, 'YEARLY': 365,
}


def parse_and_validate(message, tz) -> list[dict]:
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

    return [v for e in events if (v := _validate_event(e, tz)) is not None]


def _validate_event(event: dict, tz) -> dict | None:
    for field in ('title', 'start', 'end'):
        if not event.get(field):
            return None

    def local_to_utc(dt_str: str) -> str:
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is not None:
                return dt.astimezone(dt_timezone.utc).isoformat()
            return dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).isoformat()
        except (ValueError, TypeError):
            return dt_str

    start = local_to_utc(event['start'])
    end = local_to_utc(event['end'])

    recurrence_freq = event.get('recurrence_freq', '').strip().upper()
    if recurrence_freq not in VALID_FREQS:
        recurrence_freq = ''

    if recurrence_freq:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            duration_days = (end_dt - start_dt).total_seconds() / 86400
            if duration_days >= RECURRENCE_MIN_INTERVAL_DAYS[recurrence_freq]:
                recurrence_freq = ''
        except (ValueError, TypeError):
            recurrence_freq = ''

    recurrence_until = event.get('recurrence_until', '').strip() if recurrence_freq else ''

    status = event.get('status', 'active').strip().lower()
    if status not in ('active', 'pending'):
        status = 'active'

    concern = event.get('concern', '').strip() if status == 'pending' else ''
    if status == 'pending' and not concern:
        concern = 'Needs review.'
    expires_at = event.get('expires_at', '').strip() if status == 'pending' else ''

    if expires_at:
        try:
            datetime.fromisoformat(expires_at)
        except ValueError:
            expires_at = ''

    return {
        'title': str(event.get('title', '')).strip()[:255],
        'description': str(event.get('description', '')).strip(),
        'start': start,
        'end': end,
        'category_hint': str(event.get('category_hint', '')).strip(),
        'recurrence_freq': recurrence_freq,
        'recurrence_until': recurrence_until,
        'status': status,
        'concern': concern,
        'expires_at': expires_at,
    }
