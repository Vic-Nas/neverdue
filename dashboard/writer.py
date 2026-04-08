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


def _resolve_reminders(event_reminders: list, category) -> list[dict]:
    """Return GCal-formatted reminder overrides.

    *event_reminders* takes precedence; if empty, fall back to category.
    Both are stored as ``[minutes, ...]`` (list of ints).
    """
    minutes_list = event_reminders or (category.reminders if category else [])
    return [{'method': 'popup', 'minutes': int(m)} for m in minutes_list]


def _gcal_source_for_links(links: list, event_data: dict, event_pk: int | None = None) -> dict | None:
    """Return a GCal source dict for the given links.

    - 0 links → None
    - 1 link  → use it directly
    - 2+ links → point to the NeverDue links page (requires saved event PK)
    """
    if not links:
        return None
    if len(links) == 1:
        return {'title': links[0].get('title') or event_data['title'], 'url': links[0]['url']}
    if event_pk:
        return {
            'title': f"Links for: {event_data['title']}",
            'url': f"https://{settings.DOMAIN}/events/{event_pk}/links/",
        }
    return None  # will be patched after save


def build_gcal_body(event) -> dict:
    """Build a GCal API request body from an Event model instance."""
    user = event.user
    category = event.category
    reminders = _resolve_reminders(event.reminders, category)

    body = {
        'summary': event.title,
        'description': event.description or '',
        'start': {'dateTime': event.start.strftime('%Y-%m-%dT%H:%M:%SZ'), 'timeZone': 'UTC'},
        'end': {'dateTime': event.end.strftime('%Y-%m-%dT%H:%M:%SZ'), 'timeZone': 'UTC'},
        'reminders': {'useDefault': False, 'overrides': reminders},
        'colorId': _resolve_color_id(user, category, event.color),
    }
    if event.recurrence_freq:
        body['recurrence'] = [_build_rrule(event.recurrence_freq, event.recurrence_until)]
    links = event.links or []
    source = _gcal_source_for_links(links, {'title': event.title}, event.pk)
    if source:
        body['source'] = source
    return body


def _build_gcal_body_from_dict(user, event_data: dict, category, event_pk: int | None = None) -> dict:
    """Build GCal API request body from an event data dict (pre-save)."""
    reminders = _resolve_reminders([], category)

    body = {
        'summary': event_data['title'],
        'description': event_data.get('description', ''),
        'start': {'dateTime': event_data['start'], 'timeZone': 'UTC'},
        'end': {'dateTime': event_data['end'], 'timeZone': 'UTC'},
        'reminders': {'useDefault': False, 'overrides': reminders},
        'colorId': _resolve_color_id(user, category),
    }
    links = event_data.get('links', [])
    source = _gcal_source_for_links(links, event_data, event_pk)
    if source:
        body['source'] = source
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
            links=event_data.get('links', []),
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

            links = event_data.get('links', [])
            needs_patch = len(links) > 1  # source URL requires saved PK, patch after

            body = _build_gcal_body_from_dict(user, event_data, category, event_pk=None)
            google_event = svc.events().insert(calendarId='primary', body=body).execute()
            google_event_id = google_event.get('id')
            gcal_link = google_event.get('htmlLink', '')

            if needs_patch:
                # Now we have the GCal event ID but not yet the DB PK.
                # Save to DB first, then patch GCal source with the real event PK.
                pass  # handled below after Event.objects.create

        except Exception as exc:
            logger.error("dashboard.write_event_to_calendar: gcal push failed | user=%s error=%s", user.pk, exc)
            raise GCalUnavailableError(str(exc)) from exc
    else:
        desc = event_data.get('description', '')
        note = 'Not synced to Google Calendar (disabled in Preferences).'
        event_data['description'] = f'{desc}\n\n{note}'.strip() if desc else note

    try:
        event = Event.objects.create(
            user=user, category=category,
            title=event_data['title'], description=event_data.get('description', ''),
            links=event_data.get('links', []),
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

    # Patch GCal source now that we have the DB PK
    if user.save_to_gcal and google_event_id and len(event_data.get('links', [])) > 1:
        try:
            from dashboard.gcal.client import _service
            svc = _service(user)
            source = _gcal_source_for_links(event_data['links'], event_data, event.pk)
            svc.events().patch(
                calendarId='primary',
                eventId=google_event_id,
                body={'source': source},
            ).execute()
        except Exception as exc:
            logger.warning("dashboard.write_event_to_calendar: gcal source patch failed | user=%s error=%s", user.pk, exc)
            # Non-fatal — event is saved, source just won't be set

    return event