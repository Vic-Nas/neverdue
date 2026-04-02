import logging

from django.conf import settings
from googleapiclient.errors import HttpError

from .client import _service

logger = logging.getLogger(__name__)


def delete_from_gcal(user, google_event_id: str) -> bool:
    if not google_event_id:
        return False
    try:
        svc = _service(user)
        svc.events().delete(calendarId='primary', eventId=google_event_id).execute()
        return True
    except HttpError as exc:
        if exc.resp.status == 404:
            return True  # already gone
        logger.warning("dashboard.delete_from_gcal: api error | status=%s user=%s", exc.resp.status, user.pk)
        return False
    except Exception as exc:
        logger.warning("dashboard.delete_from_gcal: failed | user=%s error=%s", user.pk, exc)
        return False


def push_event_to_gcal(user, event):
    """Create a new GCal event. Returns (html_link, gcal_id) or None."""
    from dashboard.writer import _build_gcal_body
    try:
        svc = _service(user)
        body = _build_gcal_body(event)
        data = svc.events().insert(calendarId='primary', body=body).execute()
        if settings.DEBUG:
            logger.debug("dashboard.push_event_to_gcal: created | user=%s", user.pk)
        return data.get('htmlLink', ''), data.get('id', '')
    except Exception as exc:
        logger.warning("dashboard.push_event_to_gcal: failed | user=%s error=%s", user.pk, exc)
        return None


def update_event_in_gcal(user, event) -> bool:
    """Patch an existing GCal event. Returns True on success."""
    from dashboard.writer import _build_gcal_body
    if not event.google_event_id:
        return False
    try:
        svc = _service(user)
        body = _build_gcal_body(event)
        svc.events().patch(calendarId='primary', eventId=event.google_event_id, body=body).execute()
        return True
    except Exception as exc:
        logger.warning("dashboard.update_event_in_gcal: failed | user=%s error=%s", user.pk, exc)
        return False


def patch_event_color(user, google_event_id: str, color_id: str) -> bool:
    if not google_event_id:
        return False
    try:
        svc = _service(user)
        svc.events().patch(calendarId='primary', eventId=google_event_id, body={'colorId': color_id}).execute()
        return True
    except Exception as exc:
        logger.warning("dashboard.patch_event_color: failed | user=%s error=%s", user.pk, exc)
        return False
