# dashboard/writer.py
import logging

import requests
from django.conf import settings

from accounts.utils import get_valid_token
from dashboard.models import Event, Category

logger = logging.getLogger(__name__)


def write_event_to_calendar(user, event_data: dict, category: Category | None = None) -> Event | None:
    """
    Write a single event to Google Calendar and save to DB.
    Returns the saved Event or None on failure or duplicate.
    """
    start = event_data.get('start')
    end = event_data.get('end')

    if settings.DEBUG:
        logger.debug(
            "[DEBUG] write_event_to_calendar | user=%s | title=%r | start=%s | end=%s | category=%s",
            user.pk,
            event_data.get('title'),
            start,
            end,
            category.name if category else None,
        )

    # Hard dedup: same user + start + end already exists → skip
    if start and end:
        if Event.objects.filter(user=user, start=start, end=end).exists():
            logger.info("Duplicate event skipped | user=%s | start=%s | end=%s", user.pk, start, end)
            return None

    try:
        token = get_valid_token(user)
    except ValueError as exc:
        logger.warning("get_valid_token failed for user=%s: %s", user.pk, exc)
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

    if settings.DEBUG:
        logger.debug("[DEBUG] POSTing to Google Calendar API | body=%s", body)

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
            "Google Calendar API error | user=%s | status=%s | response=%s",
            user.pk, response.status_code, response.text,
        )
        return None

    google_event = response.json()

    if settings.DEBUG:
        logger.debug("[DEBUG] Google Calendar event created | google_event_id=%s", google_event.get('id'))

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

    logger.info("Event created | user=%s | event_id=%s | title=%r | start=%s", user.pk, event.pk, event.title, event.start)
    return event


def _hex_to_google_color(hex_color: str) -> str:
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