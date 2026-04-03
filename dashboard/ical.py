# dashboard/ical.py
from icalendar import Calendar, Event as VEvent, vText
from datetime import datetime, timezone as dt_timezone


# RFC 5545 PRIORITY: 1=highest, 9=lowest, 0=undefined.
# Maps Category.priority (1=low … 4=urgent) to iCal PRIORITY value.
_ICAL_PRIORITY = {
    1: 9,  # low
    2: 5,  # medium
    3: 3,  # high
    4: 1,  # urgent
}


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

        vevent.add('uid', vText(f'{event.pk}@service.neverdue.ca'))
        vevent.add('summary', event.title)

        if event.description:
            vevent.add('description', event.description)

        start = _ensure_utc(event.start)
        end = _ensure_utc(event.end)
        vevent.add('dtstart', start)
        vevent.add('dtend', end)

        if event.rrule:
            vevent.add('rrule', _parse_rrule(event.rrule.removeprefix('RRULE:')))

        if event.category:
            vevent.add('categories', [event.category.name])
            ical_priority = _ICAL_PRIORITY.get(event.category.priority, 0)
            vevent.add('priority', ical_priority)

        vevent.add('status', 'CONFIRMED')
        vevent.add('dtstamp', datetime.now(tz=dt_timezone.utc))

        cal.add_component(vevent)

    return cal.to_ical()


def _ensure_utc(dt) -> datetime:
    if dt is None:
        return datetime.now(tz=dt_timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def _parse_rrule(rrule_str: str) -> dict:
    """
    Parse RRULE value string into a dict the icalendar library accepts.
    UNTIL must be a datetime object — not a string — or the library raises TypeError.
    """
    parts = {}
    for part in rrule_str.split(';'):
        if '=' not in part:
            continue
        key, value = part.split('=', 1)
        key = key.strip().upper()

        if key == 'UNTIL':
            try:
                clean = value.strip().rstrip('Z')
                if 'T' in clean:
                    dt = datetime.strptime(clean, '%Y%m%dT%H%M%S').replace(tzinfo=dt_timezone.utc)
                else:
                    dt = datetime.strptime(clean, '%Y%m%d').replace(tzinfo=dt_timezone.utc)
                parts[key] = dt
            except ValueError:
                pass  # skip malformed UNTIL rather than crash
        else:
            parts[key] = value.strip()

    return parts
