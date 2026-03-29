# dashboard/writer.py
import logging

import requests
from django.conf import settings

from accounts.utils import get_valid_token
from dashboard.models import Event, Category

logger = logging.getLogger(__name__)


# Maps Category.priority (1–4) to the user preference field name.
_PRIORITY_FIELD = {
    1: 'priority_color_low',
    2: 'priority_color_medium',
    3: 'priority_color_high',
    4: 'priority_color_urgent',
}


def _build_rrule(freq: str, until) -> str:
    """
    Build a Google Calendar-compatible RRULE string.
    `until` is a date or None. Appends T000000Z suffix as required by GCal.
    """
    rule = f"RRULE:FREQ={freq}"
    if until:
        date_str = until.strftime("%Y%m%d") if hasattr(until, "strftime") else until.replace("-", "")
        rule += f";UNTIL={date_str}T000000Z"
    return rule


def _priority_color_id(user, priority: int) -> str:
    field = _PRIORITY_FIELD.get(priority, 'priority_color_low')
    return str(getattr(user, field, 2))


def _resolve_color_id(user, category, event_color: str = '') -> str:
    if event_color:
        return str(event_color)
    if category and category.gcal_color_id:
        return category.gcal_color_id
    return _priority_color_id(user, category.priority if category else 1)


def _build_gcal_body(event) -> dict:
    """Build GCal API request body from an Event instance."""
    reminders = []
    if event.category and event.category.reminders:
        reminders = [
            {'method': 'popup', 'minutes': r['minutes']}
            for r in event.category.reminders
        ]

    body = {
        'summary': event.title,
        'description': event.description or '',
        'start': {'dateTime': event.start.isoformat(), 'timeZone': 'UTC'},
        'end': {'dateTime': event.end.isoformat(), 'timeZone': 'UTC'},
        'reminders': {'useDefault': False, 'overrides': reminders},
        'colorId': _resolve_color_id(event.user, event.category, event.color),
    }

    if event.recurrence_freq:
        body['recurrence'] = [_build_rrule(event.recurrence_freq, event.recurrence_until)]

    return body


def write_event_to_calendar(user, event_data: dict, category: Category | None = None, scan_job=None) -> Event | None:
    """
    Write a single event to the DB and optionally Google Calendar.
    Pending events are saved to DB only — not pushed to Google Calendar.
    Returns the saved Event or None on failure or duplicate.
    """
    start = event_data.get('start')
    end = event_data.get('end')
    status = event_data.get('status', 'active')

    # Hard dedup: same user + start + end already exists → skip
    if start and end:
        if Event.objects.filter(user=user, start=start, end=end, status='active').exists():
            if settings.DEBUG:
                logger.debug("dashboard.write_event_to_calendar: duplicate skipped | user=%s", user.pk)
            return None

    # Pending events: save to DB only, no Google Calendar push
    if status == 'pending':
        from datetime import date
        expires_at_raw = event_data.get('expires_at', '')
        expires_at = None
        if expires_at_raw:
            try:
                expires_at = date.fromisoformat(expires_at_raw)
            except ValueError:
                expires_at = None

        try:
            event = Event.objects.create(
                user=user,
                category=category,
                title=event_data['title'],
                description=event_data.get('description', ''),
                start=event_data['start'],
                end=event_data['end'],
                recurrence_freq=event_data.get('recurrence_freq') or None,
                recurrence_until=event_data.get('recurrence_until') or None,
                source_email_id=event_data.get('source_email_id'),
                status='pending',
                pending_concern=event_data.get('concern', ''),
                pending_expires_at=expires_at,
                scan_job=scan_job,
            )
            if settings.DEBUG:
                logger.debug("dashboard.write_event_to_calendar: pending saved | user=%s", user.pk)
            return event
        except Exception as exc:
            logger.error("dashboard.write_event_to_calendar: pending save failed | user=%s error=%s",
                         user.pk, exc)
            return None

    # Active events: push to Google Calendar then save to DB
    try:
        token = get_valid_token(user)
    except ValueError as exc:
        logger.warning("dashboard.write_event_to_calendar: token unavailable | user=%s", user.pk)
        return None

    reminders = []
    if category and category.reminders:
        reminders = [
            {'method': 'popup', 'minutes': r['minutes']}
            for r in category.reminders
        ]

    body = {
        'summary': event_data['title'],
        'description': event_data.get('description', ''),
        'start': {'dateTime': event_data['start'], 'timeZone': 'UTC'},
        'end': {'dateTime': event_data['end'], 'timeZone': 'UTC'},
        'reminders': {
            'useDefault': False,
            'overrides': reminders,
        },
        # Color is always set: driven by the category's priority level and the
        # user's priority color preferences, not the category's hex display color.
        'colorId': _resolve_color_id(user, category),
    }

    if event_data.get('recurrence_freq'):
        body['recurrence'] = [_build_rrule(
            event_data['recurrence_freq'],
            event_data.get('recurrence_until'),
        )]

    try:
        response = requests.post(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            json=body,
        )

        if response.status_code not in (200, 201):
            logger.error(
                "dashboard.write_event_to_calendar: api error | user=%s status=%s",
                user.pk, response.status_code,
            )
            return None

        google_event = response.json()
        if settings.DEBUG:
            logger.debug("dashboard.write_event_to_calendar: pushed to gcal | user=%s", user.pk)
    except Exception as exc:
        logger.error("dashboard.write_event_to_calendar: request error | user=%s error=%s", user.pk, exc)
        return None

    event = Event.objects.create(
        user=user,
        category=category,
        title=event_data['title'],
        description=event_data.get('description', ''),
        start=event_data['start'],
        end=event_data['end'],
        recurrence_freq=event_data.get('recurrence_freq') or None,
        recurrence_until=event_data.get('recurrence_until') or None,
        google_event_id=google_event.get('id'),
        source_email_id=event_data.get('source_email_id'),
        status='active',
        gcal_link=google_event.get('htmlLink', ''),
        scan_job=scan_job,
    )

    if settings.DEBUG:
        logger.debug("dashboard.write_event_to_calendar: saved | user=%s", user.pk)
    return event