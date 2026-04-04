# dashboard/webhook.py
import logging
from datetime import timedelta
from email.utils import parsedate_to_datetime

from django.http import HttpResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import User
from dashboard.models import Event

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def gcal_webhook(request):
    channel_id = request.headers.get('X-Goog-Channel-ID', '')
    state = request.headers.get('X-Goog-Resource-State', '')

    if state == 'sync':
        return HttpResponse(status=200)
    if state != 'exists':
        return HttpResponse(status=200)

    try:
        user = User.objects.get(gcal_channel_id=channel_id)
    except User.DoesNotExist:
        logger.debug("dashboard.gcal_webhook: stale channel ignored | channel_id=%s", channel_id)
        return HttpResponse(status=200)

    _sync_changed_events(user)

    expiry_str = request.headers.get('X-Goog-Channel-Expiration', '')
    if expiry_str:
        try:
            expiry = parsedate_to_datetime(expiry_str)
            if expiry - dj_timezone.now() < timedelta(days=2):
                from dashboard.gcal import register_gcal_watch
                register_gcal_watch(user)
        except Exception:
            pass

    return HttpResponse(status=200)


def _sync_changed_events(user):
    from dashboard.gcal.client import _service
    try:
        svc = _service(user)
    except Exception as exc:
        logger.error("dashboard._sync_changed_events: token failed | user_id=%s error=%s", user.pk, exc)
        return

    updated_min = (dj_timezone.now() - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        result = svc.events().list(
            calendarId='primary',
            updatedMin=updated_min,
            maxResults=50,
            singleEvents=True,
            orderBy='updated',
        ).execute()
    except Exception as exc:
        logger.error("dashboard._sync_changed_events: api error | user_id=%s error=%s", user.pk, exc)
        return

    for item in result.get('items', []):
        gcal_id = item.get('id')
        if not gcal_id:
            continue
        try:
            event = Event.objects.get(user=user, google_event_id=gcal_id)
        except Event.DoesNotExist:
            continue

        # Cancelled in GCal → delete locally
        if item.get('status') == 'cancelled':
            event._skip_gcal_delete = True
            event.delete()
            logger.info("_sync_changed_events: deleted cancelled event | user=%s gcal_id=%s", user.pk, gcal_id)
            continue

        update_fields = []

        # Title
        new_title = item.get('summary', '')
        if new_title and new_title != event.title:
            event.title = new_title
            update_fields.append('title')

        # Description
        new_desc = item.get('description', '') or ''
        if new_desc != (event.description or ''):
            event.description = new_desc
            update_fields.append('description')

        # Color
        new_color = item.get('colorId', '')
        if new_color != event.color:
            event.color = new_color
            update_fields.append('color')

        # Link
        new_link = item.get('htmlLink', '')
        if new_link and new_link != event.gcal_link:
            event.gcal_link = new_link
            update_fields.append('gcal_link')

        # Start / End
        new_start = _parse_gcal_datetime(item.get('start'))
        new_end = _parse_gcal_datetime(item.get('end'))
        if new_start and new_start != event.start:
            event.start = new_start
            update_fields.append('start')
        if new_end and new_end != event.end:
            event.end = new_end
            update_fields.append('end')

        # Reminders
        new_reminders = _parse_gcal_reminders(item.get('reminders'))
        if new_reminders is not None and sorted(new_reminders) != sorted(event.reminders):
            event.reminders = new_reminders
            update_fields.append('reminders')

        # Recurrence (from original event — singleEvents=True expands,
        # so recurringEventId presence tells us this is an instance;
        # we skip recurrence changes for instances).
        if not item.get('recurringEventId'):
            new_freq, new_until = _parse_gcal_recurrence(item.get('recurrence'))
            if new_freq != event.recurrence_freq:
                event.recurrence_freq = new_freq
                update_fields.append('recurrence_freq')
            if new_until != event.recurrence_until:
                event.recurrence_until = new_until
                update_fields.append('recurrence_until')

        if update_fields:
            Event.objects.filter(pk=event.pk).update(
                **{f: getattr(event, f) for f in update_fields}
            )
            logger.info(
                "_sync_changed_events: updated %s | user=%s event=%s",
                update_fields, user.pk, event.pk,
            )


def _parse_gcal_datetime(dt_obj):
    """Parse a GCal start/end object into a timezone-aware datetime."""
    if not dt_obj:
        return None
    from django.utils.dateparse import parse_datetime as django_parse
    raw = dt_obj.get('dateTime')
    if raw:
        return django_parse(raw)
    # All-day event: date only → midnight UTC
    raw = dt_obj.get('date')
    if raw:
        from datetime import datetime, timezone as dt_tz
        try:
            return datetime.strptime(raw, '%Y-%m-%d').replace(tzinfo=dt_tz.utc)
        except ValueError:
            return None
    return None


def _parse_gcal_reminders(rem_obj) -> list | None:
    """Extract reminder minutes from GCal reminders object.

    Returns ``None`` when the data is absent (= no change detected).
    Returns ``[]`` when useDefault is True (= clear overrides).
    """
    if not rem_obj:
        return None
    if rem_obj.get('useDefault', False):
        return []
    overrides = rem_obj.get('overrides', [])
    return [int(r.get('minutes', 0)) for r in overrides]


def _parse_gcal_recurrence(recurrence_list):
    """Parse GCal recurrence list (e.g. ['RRULE:FREQ=WEEKLY;UNTIL=...'])."""
    freq = None
    until = None
    if not recurrence_list:
        return freq, until
    for rule in recurrence_list:
        if not rule.startswith('RRULE:'):
            continue
        parts = rule[6:].split(';')
        for part in parts:
            if part.startswith('FREQ='):
                freq = part[5:]
            elif part.startswith('UNTIL='):
                raw = part[6:]
                from datetime import date
                try:
                    until = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
                except (ValueError, IndexError):
                    pass
    return freq, until
