# dashboard/writer.py
import requests
from accounts.utils import get_valid_token
from dashboard.models import Event, Category


def write_event_to_calendar(user, event_data: dict, category: Category | None = None) -> Event | None:
    """
    Write a single event to Google Calendar and save to DB.
    Returns the saved Event or None on failure.
    """
    try:
        token = get_valid_token(user)
    except ValueError:
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
    }

    if event_data.get('recurrence_freq'):
        rrule = f"RRULE:FREQ={event_data['recurrence_freq']}"
        if event_data.get('recurrence_until'):
            until = event_data['recurrence_until'].replace('-', '')
            rrule += f';UNTIL={until}'
        body['recurrence'] = [rrule]

    if category and category.color:
        body['colorId'] = _hex_to_google_color(category.color)

    response = requests.post(
        'https://www.googleapis.com/calendar/v3/calendars/primary/events',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        json=body,
    )

    if response.status_code not in (200, 201):
        return None

    google_event = response.json()

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
    )

    return event


def _hex_to_google_color(hex_color: str) -> str:
    """
    Maps a hex color to the closest Google Calendar colorId (1-11).
    Falls back to '1' (lavender) if no match.
    """
    mapping = {
        '#7986cb': '1',  # lavender
        '#33b679': '2',  # sage
        '#8e24aa': '3',  # grape
        '#e67c73': '4',  # flamingo
        '#f6c026': '5',  # banana
        '#f5511d': '6',  # tangerine
        '#039be5': '7',  # peacock
        '#616161': '8',  # graphite
        '#3f51b5': '9',  # blueberry
        '#0b8043': '10', # basil
        '#d60000': '11', # tomato
    }
    return mapping.get(hex_color.lower(), '1')