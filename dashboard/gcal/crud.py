# dashboard/gcal/crud.py
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
