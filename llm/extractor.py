# llm/extractor.py
import json
import re
import zoneinfo
from datetime import datetime, timezone as dt_timezone
import anthropic
from django.conf import settings
from django.utils import timezone

client = anthropic.Anthropic(api_key=settings.LLM_API_KEY)

SYSTEM_PROMPT = """You are a calendar event extractor. Given text content from an email or document, extract all calendar events, deadlines, and scheduled items.

Return ONLY a valid JSON array. No explanation, no markdown, no extra text.

Today's date and the user's local timezone will be provided in the user message. All times mentioned in the source content should be interpreted as being in that local timezone unless the content explicitly states a different timezone. Output all datetimes in that same local timezone (no UTC conversion — just use the times as written or implied by the content).

Each event must have:
- "title": concise event name (string)
- "description": relevant context from the source (string, can be empty)
- "start": ISO 8601 datetime string WITHOUT timezone offset, in the user's local time (e.g. "2025-09-15T09:00:00")
- "end": ISO 8601 datetime string WITHOUT timezone offset, in the user's local time (must be after start)
- "category_hint": suggested category name based on context (string, can be empty)
- "recurrence_freq": recurrence frequency if clearly stated — one of "DAILY", "WEEKLY", "MONTHLY", "YEARLY" or empty string if not recurring or uncertain
- "recurrence_until": end date for recurrence as "YYYY-MM-DD" string, or empty string if open-ended or not applicable
- "status": either "active" or "pending"
- "concern": if status is "pending", explain briefly what information is missing or ambiguous (string, empty if status is "active")
- "expires_at": if status is "pending", the date after which this event is no longer relevant, as "YYYY-MM-DD". Use the earliest date you are certain about from the content (e.g. if an event clearly ends before March 10, set expires_at to March 11). Empty string if not determinable.

Rules:
- If only a date is given with no time, set start to 09:00 and end to 10:00 on that date
- If a deadline is mentioned with no end time, set end to 1 hour after start
- YEAR INFERENCE: When no year is given, always use the year from today's date provided. Only advance to the next year if the resulting date would be in the past relative to today (e.g. today is Nov 2026, event says "March 15" → use March 15 2027). Never leave the year ambiguous — always commit to a specific year.
- If no events are found, return an empty array []
- Never return null values — use empty strings instead
- Do NOT apply any UTC offset — output the local time as-is
- Only set recurrence_freq if you are highly confident — when in doubt leave it empty
- Never set recurrence_freq if the event duration would equal or exceed the recurrence interval
- If the user provides context (e.g. "weekly schedule", "repeats until April 23", "ignore prices"), follow it strictly. Context overrides your own inference.
- When reading a table or grid, treat each column and row independently. Read the date from each column header and apply it only to events in that column. Never anchor events from multiple columns onto a single date.

When to set status "pending":
- The event looks like a recurring schedule but no recurrence end date was provided or inferable (and user context didn't provide one)
- The content is contradictory or unclear
- A one-time event's date has already passed (use today's date provided in the message)
- Critical information is missing that would affect how the event is saved

When to keep status "active":
- Simple deadline with clear date and time
- Recurring event with explicit or strongly implied end date (including from user context)
- All required information is present and unambiguous

Example output:
[
  {
    "title": "Submit thesis draft",
    "description": "Final draft due to supervisor",
    "start": "2026-11-01T09:00:00",
    "end": "2026-11-01T10:00:00",
    "category_hint": "University",
    "recurrence_freq": "",
    "recurrence_until": "",
    "status": "active",
    "concern": "",
    "expires_at": ""
  },
  {
    "title": "Weekly lecture",
    "description": "COMP 101 every Monday",
    "start": "2026-09-08T10:00:00",
    "end": "2026-09-08T11:00:00",
    "category_hint": "Courses",
    "recurrence_freq": "WEEKLY",
    "recurrence_until": "2026-12-15",
    "status": "active",
    "concern": "",
    "expires_at": ""
  }
]"""

RECONCILIATION_PROMPT = """You are a calendar event reconciler. You are given:
1. A list of events already extracted from attachments (their dates and times are ground truth — do not modify them)
2. An email body and any non-calendar attachments that may provide additional context

Your job is to produce a final merged event list. Apply these rules strictly in order:

RECURRENCE: If the body states that a schedule repeats (e.g. "every week", "weekly until X", "repeats from date A to date B"), apply recurrence_freq and recurrence_until to all matching extracted events. A past start date is NOT a reason to mark an event pending if it has a future recurrence_until — set status "active". This is the most important rule.

CATEGORY: If the body or filename provides category context (e.g. "exams calendar", "weekly courses"), override category_hint on matching events accordingly. Exam events must use a hint like "Examens", course events "Cours", etc.

ENRICHMENT: Add context from the body to descriptions (location, instructor, notes) without changing dates or times.

COMPLEMENTARY ATTACHMENTS: Fold info from non-calendar attachments (room lists, etc.) into event descriptions.

DEDUPLICATION: Merge events with the same title and same start time into one, keeping the most complete version.

NEW EVENTS: Add events mentioned only in the body that are not covered by the extracted events.

CONFLICTS: If the body contradicts an extracted event's date or time, do NOT override — set status "pending" and explain in "concern".

Return ONLY a valid JSON array using the same schema as the input events. No explanation, no markdown, no extra text. Never return null values — use empty strings instead."""


# Filename stems that carry no useful information for the LLM.
_JUNK_STEMS = frozenset({
    'screenshot', 'screen shot', 'image', 'img', 'photo', 'pic', 'picture',
    'scan', 'scanned', 'document', 'doc', 'file', 'attachment', 'attach',
    'untitled', 'unnamed', 'noname', 'new', 'copy', 'temp', 'tmp',
    'download', 'export', 'output',
})

_RE_UUID = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)
_RE_TIMESTAMP = re.compile(r'^[\d_\-T:.]+$')


def _is_informative_filename(filename: str) -> bool:
    if not filename:
        return False
    stem = filename.rsplit('.', 1)[0].strip()
    if len(stem) <= 4:
        return False
    if _RE_UUID.match(stem):
        return False
    if _RE_TIMESTAMP.match(stem):
        return False
    words = re.split(r'[\s_\-.()\[\]]+', stem.lower())
    non_numeric_words = [w for w in words if w and not w.isdigit()]
    if not non_numeric_words:
        return False
    if all(w in _JUNK_STEMS for w in non_numeric_words):
        return False
    return True


def _get_tz(tz_name: str) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return dt_timezone.utc


def _today_in_tz(tz: zoneinfo.ZoneInfo | dt_timezone) -> str:
    return datetime.now(tz=tz).date().isoformat()


def extract_events(text: str, language: str = 'English', user_timezone: str = 'UTC') -> list[dict]:
    tz = _get_tz(user_timezone)
    today = _today_in_tz(tz)
    system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    message = client.messages.create(
        model=settings.LLM_MODEL,
        max_tokens=2000,
        system=system,
        messages=[
            {
                'role': 'user',
                'content': (
                    f"Today's date: {today}\n"
                    f"User's timezone: {user_timezone}\n\n"
                    f"Extract all calendar events from this content:\n\n{text}"
                )
            }
        ]
    )

    return _parse_and_validate(message, tz)


def extract_events_from_image(
    file_bytes: bytes,
    media_type: str,
    context: str = '',
    language: str = 'English',
    user_timezone: str = 'UTC',
) -> list[dict]:
    import base64
    encoded = base64.standard_b64encode(file_bytes).decode('utf-8')

    if media_type == 'application/pdf':
        content_block = {'type': 'document', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}}
    else:
        content_block = {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}}

    tz = _get_tz(user_timezone)
    today = _today_in_tz(tz)
    system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    user_text = (
        f"Today's date: {today}\n"
        f"User's timezone: {user_timezone}\n\n"
        f"Extract all calendar events from this file."
    )
    if context:
        user_text += f'\n\nUser context (follow strictly): {context}'

    message = client.messages.create(
        model=settings.LLM_MODEL,
        max_tokens=2000,
        system=system,
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

    return _parse_and_validate(message, tz)


def extract_events_from_email(
    body: str,
    attachments: list[tuple[bytes, str, str]],
    language: str = 'English',
    user_timezone: str = 'UTC',
) -> list[dict]:
    """
    Extract calendar events from an email body + attachments using a two-step approach:
    1. Extract events from each attachment independently (dates from images are ground truth)
    2. Reconcile with the email body and any non-visual attachments for enrichment
    """
    import base64

    tz = _get_tz(user_timezone)
    today = _today_in_tz(tz)
    system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    # ── Step 1: extract from each attachment independently ──
    attachment_events: list[dict] = []
    non_visual_attachments: list[tuple[bytes, str, str]] = []

    for file_bytes, media_type, filename in attachments:
        if media_type in ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'):
            content = []
            if _is_informative_filename(filename):
                content.append({'type': 'text', 'text': f'Attachment filename: {filename}'})
            encoded = base64.standard_b64encode(file_bytes).decode('utf-8')
            if media_type == 'application/pdf':
                content.append({'type': 'document', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}})
            else:
                content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}})
            content.append({'type': 'text', 'text': (
                f"Today's date: {today}\n"
                f"User's timezone: {user_timezone}\n\n"
                f"Extract all calendar events from this file."
            )})

            try:
                message = client.messages.create(
                    model=settings.LLM_MODEL,
                    max_tokens=2000,
                    system=system,
                    messages=[{'role': 'user', 'content': content}]
                )
                attachment_events.extend(_parse_and_validate(message, tz))
            except (ValueError, Exception):
                pass
        else:
            non_visual_attachments.append((file_bytes, media_type, filename))

    # If no body and no non-visual attachments, skip reconciliation
    if not body and not non_visual_attachments:
        return attachment_events

    # ── Step 2: reconcile with body and non-visual attachments ──
    recon_content = []

    # Non-visual attachments (e.g. plain text room lists)
    for file_bytes, media_type, filename in non_visual_attachments:
        if _is_informative_filename(filename):
            recon_content.append({'type': 'text', 'text': f'Attachment filename: {filename}'})
        encoded = base64.standard_b64encode(file_bytes).decode('utf-8')
        if media_type == 'application/pdf':
            recon_content.append({'type': 'document', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}})
        else:
            recon_content.append({'type': 'text', 'text': file_bytes.decode('utf-8', errors='ignore')})

    recon_text = (
        f"Today's date: {today}\n"
        f"User's timezone: {user_timezone}\n\n"
    )
    if attachment_events:
        recon_text += (
            f"Events already extracted from attachments (dates and times are ground truth):\n"
            f"{json.dumps(attachment_events, ensure_ascii=False)}\n\n"
        )
    if body:
        if attachment_events:
            recon_text += f"Email body:\n{body}"
        else:
            recon_text += f"Extract all calendar events from this content:\n\n{body}"

    recon_content.append({'type': 'text', 'text': recon_text})

    if attachment_events:
        recon_system = RECONCILIATION_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'
    else:
        recon_system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    try:
        message = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=2000,
            system=recon_system,
            messages=[{'role': 'user', 'content': recon_content}]
        )
        return _parse_and_validate(message, tz)
    except (ValueError, Exception):
        # Reconciliation failed — return what we have from attachments
        return attachment_events


def _parse_and_validate(message, tz) -> list[dict]:
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


VALID_FREQS = {'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'}

RECURRENCE_MIN_INTERVAL_DAYS = {
    'DAILY': 1,
    'WEEKLY': 7,
    'MONTHLY': 30,
    'YEARLY': 365,
}


def _validate_event(event: dict, tz) -> dict | None:
    required = ('title', 'start', 'end')
    for field in required:
        if not event.get(field):
            return None

    def local_to_utc(dt_str: str) -> str:
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is not None:
                return dt.astimezone(dt_timezone.utc).isoformat()
            dt_local = dt.replace(tzinfo=tz)
            return dt_local.astimezone(dt_timezone.utc).isoformat()
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
            min_days = RECURRENCE_MIN_INTERVAL_DAYS[recurrence_freq]
            if duration_days >= min_days:
                recurrence_freq = ''
        except (ValueError, TypeError):
            recurrence_freq = ''

    recurrence_until = event.get('recurrence_until', '').strip() if recurrence_freq else ''

    status = event.get('status', 'active').strip().lower()
    if status not in ('active', 'pending'):
        status = 'active'

    concern = event.get('concern', '').strip() if status == 'pending' else ''
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