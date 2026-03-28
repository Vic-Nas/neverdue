# dashboard/gcal.py
#
# Two things in one place:
#   1. delete_from_gcal()  — reusable helper, called directly or via signal
#   2. pre_delete signal   — fires before ANY Event deletion, regardless of cause
#                            (bulk delete, category cascade, reprocess, cleanup)
#
# Wire the signal by importing this module in dashboard/apps.py ready() method.

import logging
import requests
from django.db.models.signals import pre_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def delete_from_gcal(user, google_event_id: str) -> bool:
    """
    Delete a single event from Google Calendar.
    Returns True on success or 404 (already gone), False on any other failure.
    Safe to call even if the token is expired — logs and returns False.
    """
    from accounts.utils import get_valid_token

    if not google_event_id:
        return False

    try:
        token = get_valid_token(user)
    except Exception as exc:
        logger.warning("delete_from_gcal: get_valid_token failed for user=%s: %s", user.pk, exc)
        return False

    try:
        response = requests.delete(
            f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if response.status_code in (204, 404):
            # 204 = deleted, 404 = already gone — both are fine
            logger.info("delete_from_gcal: removed google_event_id=%s for user=%s", google_event_id, user.pk)
            return True
        else:
            logger.warning(
                "delete_from_gcal: unexpected status %s for google_event_id=%s user=%s — %s",
                response.status_code, google_event_id, user.pk, response.text,
            )
            return False
    except Exception as exc:
        logger.warning("delete_from_gcal: request failed for google_event_id=%s user=%s: %s",
                       google_event_id, user.pk, exc)
        return False


# ---------------------------------------------------------------------------
# Signal — runs before every Event deletion, whatever the cause.
# This covers:
#   - Bulk delete from dashboard (events_bulk_action)
#   - Category deletion cascade
#   - Reprocess task deleting old events
#   - Any future deletion path
#
# Cleanup task (cleanup_events) respects delete_from_gcal_on_cleanup preference
# and calls delete_from_gcal() directly — it does NOT rely on this signal so
# that the preference is honoured. The signal skips events deleted by the
# cleanup task by checking a flag set on the instance before deletion.
# ---------------------------------------------------------------------------

@receiver(pre_delete, sender='dashboard.Event')
def event_pre_delete(sender, instance, **kwargs):
    """
    Before an Event row is deleted, remove it from Google Calendar.
    Skipped for:
      - Pending events (never pushed to GCal)
      - Events with no google_event_id
      - Events flagged with _skip_gcal_delete=True (set by cleanup_events
        when delete_from_gcal_on_cleanup is False, to avoid double-deleting
        or deleting when the user hasn't opted in)
    """
    if getattr(instance, '_skip_gcal_delete', False):
        return

    if instance.status == 'pending':
        return

    if not instance.google_event_id:
        return

    delete_from_gcal(instance.user, instance.google_event_id)


def push_event_to_gcal(user, event):
    """Create a new GCal event. Returns (html_link, gcal_id) or None."""
    from dashboard.writer import _build_gcal_body
    from accounts.utils import get_valid_token
    try:
        token = get_valid_token(user)
    except ValueError:
        return None

    body = _build_gcal_body(event)
    response = requests.post(
        'https://www.googleapis.com/calendar/v3/calendars/primary/events',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json=body,
        timeout=10,
    )
    if response.status_code not in (200, 201):
        return None
    data = response.json()
    return data.get('htmlLink', ''), data.get('id', '')


def update_event_in_gcal(user, event) -> bool:
    """Patch an existing GCal event. Returns True on success."""
    from dashboard.writer import _build_gcal_body
    from accounts.utils import get_valid_token
    if not event.google_event_id:
        return False
    try:
        token = get_valid_token(user)
    except ValueError:
        return False

    body = _build_gcal_body(event)
    response = requests.patch(
        f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event.google_event_id}',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json=body,
        timeout=10,
    )
    return response.status_code in (200, 201)


def patch_event_color(user, google_event_id: str, color_id: str) -> bool:
    from accounts.utils import get_valid_token
    if not google_event_id:
        return False
    try:
        token = get_valid_token(user)
    except Exception as exc:
        logger.warning("patch_event_color: token failed user=%s: %s", user.pk, exc)
        return False
    response = requests.patch(
        f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json={'colorId': color_id},
        timeout=10,
    )
    if response.status_code in (200, 204):
        return True
    logger.warning("patch_event_color: status=%s event=%s user=%s",
                   response.status_code, google_event_id, user.pk)
    return False


def stop_gcal_watch(user, token: str) -> None:
    """
    Stop an existing GCal push notification channel.
    Called before registering a new one so Google stops firing the old channel_id.
    Failures are logged but never raised — stopping is best-effort.
    """
    if not user.gcal_channel_id or not user.gcal_channel_resource_id:
        return
    try:
        response = requests.post(
            'https://www.googleapis.com/calendar/v3/channels/stop',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={
                'id': user.gcal_channel_id,
                'resourceId': user.gcal_channel_resource_id,
            },
            timeout=10,
        )
        if response.status_code == 204:
            logger.info("stop_gcal_watch: stopped channel=%s user=%s",
                        user.gcal_channel_id, user.pk)
        else:
            # 404 means it already expired — that's fine
            logger.warning("stop_gcal_watch: status=%s channel=%s user=%s",
                           response.status_code, user.gcal_channel_id, user.pk)
    except Exception as exc:
        logger.warning("stop_gcal_watch: request failed user=%s: %s", user.pk, exc)


def register_gcal_watch(user) -> bool:
    """
    Register a push notification channel for the user's primary calendar.
    Stops the previous channel first so Google doesn't keep firing the old
    channel_id after renewal.
    Stores channel_id, resource_id, and expiration on the user.
    Safe to call on login or channel renewal.
    """
    import uuid
    from django.conf import settings
    from django.urls import reverse
    from accounts.utils import get_valid_token

    try:
        token = get_valid_token(user)
    except Exception as exc:
        logger.warning("register_gcal_watch: token failed user=%s: %s", user.pk, exc)
        return False

    # Stop the old channel before registering a new one
    stop_gcal_watch(user, token)

    channel_id = str(uuid.uuid4())
    webhook_path = reverse('dashboard:gcal_webhook')
    response = requests.post(
        'https://www.googleapis.com/calendar/v3/calendars/primary/events/watch',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json={
            'id': channel_id,
            'type': 'web_hook',
            'address': f'https://{settings.DOMAIN}{webhook_path}',
        },
        timeout=10,
    )
    if response.status_code != 200:
        logger.warning("register_gcal_watch: status=%s user=%s body=%s",
                       response.status_code, user.pk, response.text)
        return False

    data = response.json()
    from datetime import datetime, timezone as dt_timezone
    expiration_ms = int(data.get('expiration', 0))
    expiration_dt = datetime.fromtimestamp(expiration_ms / 1000, tz=dt_timezone.utc)

    user.gcal_channel_id = channel_id
    user.gcal_channel_resource_id = data.get('resourceId', '')
    user.gcal_channel_expiration = expiration_dt
    user.save(update_fields=['gcal_channel_id', 'gcal_channel_resource_id', 'gcal_channel_expiration'])

    logger.info("register_gcal_watch: registered channel=%s expiry=%s user=%s",
                channel_id, expiration_dt, user.pk)
    return True