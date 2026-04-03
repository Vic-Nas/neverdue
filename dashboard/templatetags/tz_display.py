# dashboard/templatetags/tz_display.py
# (create the templatetags/ directory if it doesn't exist, with an empty __init__.py)

from django import template
from datetime import datetime, timezone as dt_timezone
import zoneinfo

register = template.Library()


@register.filter
def in_user_tz(value, user):
    """
    Convert a UTC-aware datetime to the user's preferred timezone for display.

    Usage in template:
        {{ event.start|in_user_tz:request.user }}

    Returns a datetime object — format it with Django's |date or |time filters:
        {{ event.start|in_user_tz:request.user|date:"N j, Y" }}
        {{ event.start|in_user_tz:request.user|time:"g:i A" }}
    """
    if not value:
        return value
    try:
        tz_name = getattr(user, 'timezone', 'UTC') or 'UTC'
        tz = zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        tz = dt_timezone.utc

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt_timezone.utc)
        return value.astimezone(tz).replace(tzinfo=None)  # strip tzinfo so |date won't reconvert

    return value
