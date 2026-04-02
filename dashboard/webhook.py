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
        logger.error("dashboard.gcal_webhook: channel not found | channel_id=%s", channel_id)
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

        changed = False
        new_color = item.get('colorId', '')
        new_link = item.get('htmlLink', '')

        if new_color != event.color:
            event.color = new_color
            changed = True
        if new_link and new_link != event.gcal_link:
            event.gcal_link = new_link
            changed = True

        if changed:
            event.save(update_fields=['color', 'gcal_link'])
