# dashboard/writer.py
import logging

from django.conf import settings

from dashboard.models import Event, Category

logger = logging.getLogger(__name__)

_PRIORITY_FIELD = {
    1: 'priority_color_low',
    2: 'priority_color_medium',
    3: 'priority_color_high',
    4: 'priority_color_urgent',
}


def _build_rrule(freq: str, until) -> str:
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
        reminders = [{'method': 'popup', 'minutes': r['minutes']} for r in event.category.reminders]

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


def _build_gcal_body_from_dict(user, event_data: dict, category) -> dict:
    """Build GCal API request body from an event data dict (pre-save)."""
    reminders = []
    if category and category.reminders:
        reminders = [{'method': 'popup', 'minutes': r['minutes']} for r in category.reminders]

    body = {
        'summary': event_data['title'],
        'description': event_data.get('description', ''),
        'start': {'dateTime': event_data['start'], 'timeZone': 'UTC'},
        'end': {'dateTime': event_data['end'], 'timeZone': 'UTC'},
        'reminders': {'useDefault': False, 'overrides': reminders},
        'colorId': _resolve_color_id(user, category),
    }
    if event_data.get('recurrence_freq'):
        body['recurrence'] = [_build_rrule(event_data['recurrence_freq'], event_data.get('recurrence_until'))]
    return body


def write_event_to_calendar(user, event_data: dict, category: Category | None = None, scan_job=None) -> Event | None:
    """Write a single event to the DB and optionally Google Calendar."""
    start = event_data.get('start')
    end = event_data.get('end')
    status = event_data.get('status', 'active')

    if start and end:
        if Event.objects.filter(user=user, start=start, end=end, status='active').exists():
            return None

    if status == 'pending':
        return _save_pending_event(user, event_data, category, scan_job)

    return _save_active_event(user, event_data, category, scan_job)


def _save_pending_event(user, event_data, category, scan_job):
    from datetime import date
    expires_at_raw = event_data.get('expires_at', '')
    expires_at = None
    if expires_at_raw:
        try:
            expires_at = date.fromisoformat(expires_at_raw)
        except ValueError:
            pass
    try:
        return Event.objects.create(
            user=user, category=category,
            title=event_data['title'], description=event_data.get('description', ''),
            start=event_data['start'], end=event_data['end'],
            recurrence_freq=event_data.get('recurrence_freq') or None,
            recurrence_until=event_data.get('recurrence_until') or None,
            source_email_id=event_data.get('source_email_id'),
            status='pending', pending_concern=event_data.get('concern', ''),
            pending_expires_at=expires_at, scan_job=scan_job,
        )
    except Exception as exc:
        logger.error("dashboard.write_event_to_calendar: pending save failed | user=%s error=%s", user.pk, exc)
        return None


class GCalUnavailableError(Exception):
    """Raised when save_to_gcal is True but the Google token is missing."""


def _save_active_event(user, event_data, category, scan_job):
    google_event_id = None
    gcal_link = ''

    if user.save_to_gcal:
        from dashboard.gcal.client import _service
        try:
            svc = _service(user)
            body = _build_gcal_body_from_dict(user, event_data, category)
            google_event = svc.events().insert(calendarId='primary', body=body).execute()
            google_event_id = google_event.get('id')
            gcal_link = google_event.get('htmlLink', '')
        except Exception as exc:
            logger.error("dashboard.write_event_to_calendar: gcal push failed | user=%s error=%s", user.pk, exc)
            raise GCalUnavailableError(str(exc)) from exc

    try:
        return Event.objects.create(
            user=user, category=category,
            title=event_data['title'], description=event_data.get('description', ''),
            start=event_data['start'], end=event_data['end'],
            recurrence_freq=event_data.get('recurrence_freq') or None,
            recurrence_until=event_data.get('recurrence_until') or None,
            google_event_id=google_event_id,
            source_email_id=event_data.get('source_email_id'),
            status='active', gcal_link=gcal_link,
            scan_job=scan_job,
        )
    except Exception as exc:
        logger.error("dashboard.write_event_to_calendar: db save failed | user=%s error=%s", user.pk, exc)
        return None