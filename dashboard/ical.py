# dashboard/ical.py
from icalendar import Calendar, Event as VEvent, vText, vDatetime
from datetime import datetime, timezone as dt_timezone
import uuid


def build_ics(events) -> bytes:
    """
    Build an iCalendar (.ics) file from a queryset of active Event objects.
    Returns bytes ready to stream as a file download.
    """
    cal = Calendar()
    cal.add('prodid', '-//NeverDue//NeverDue//EN')
    cal.add('version', '2.0')
    cal.add('calscale', 'GREGORIAN')
    cal.add('method', 'PUBLISH')

    for event in events:
        vevent = VEvent()

        # Stable UID — use PK so reimporting the same export doesn't duplicate in most apps
        vevent.add('uid', vText(f'{event.pk}@neverdue.ca'))

        vevent.add('summary', event.title)

        if event.description:
            vevent.add('description', event.description)

        # Start / end — already UTC-aware datetimes from the DB
        start = _ensure_utc(event.start)
        end = _ensure_utc(event.end)
        vevent.add('dtstart', start)
        vevent.add('dtend', end)

        # Recurrence — reuse the rrule property already on the model
        if event.rrule:
            # icalendar library wants the raw RRULE value without the "RRULE:" prefix
            rrule_value = event.rrule.removeprefix('RRULE:')
            vevent.add('rrule', _parse_rrule(rrule_value))

        # Category
        if event.category:
            vevent.add('categories', [event.category.name])

        vevent.add('status', 'CONFIRMED')
        vevent.add('dtstamp', datetime.now(tz=dt_timezone.utc))

        cal.add_component(vevent)

    return cal.to_ical()


def _ensure_utc(dt) -> datetime:
    """Make sure a datetime is timezone-aware UTC before handing to icalendar."""
    if dt is None:
        return datetime.now(tz=dt_timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def _parse_rrule(rrule_str: str) -> dict:
    """
    Convert a raw RRULE value string like "FREQ=WEEKLY;UNTIL=20261215T000000Z"
    into the dict format the icalendar library expects for vRecur.
    """
    parts = {}
    for part in rrule_str.split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            parts[key] = value
    return parts
