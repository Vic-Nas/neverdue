# dashboard/webhook.py
import logging
import requests
from datetime import timedelta
from email.utils import parsedate_to_datetime

from django.http import HttpResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.utils import get_valid_token
from dashboard.models import Event

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def gcal_webhook(request):
    channel_id = request.headers.get('X-Goog-Channel-ID', '')
    state = request.headers.get('X-Goog-Resource-State', '')

    # sync is the initial handshake ping — acknowledge and ignore
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

    # Self-renew channel if expiring within 2 days
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
    """
    Fetch recently updated GCal events and sync color + gcal_link back to DB.
    We fetch the 50 most recently modified events — sufficient for any single change.
    """
    try:
        token = get_valid_token(user)
    except Exception as exc:
        logger.error("dashboard._sync_changed_events: token failed | user_id=%s error=%s", user.pk, exc)
        return

    # Look back 5 minutes — webhooks arrive within seconds, this is generous
    updated_min = (dj_timezone.now() - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')

    response = requests.get(
        'https://www.googleapis.com/calendar/v3/calendars/primary/events',
        headers={'Authorization': f'Bearer {token}'},
        params={
            'updatedMin': updated_min,
            'maxResults': 50,
            'singleEvents': True,
            'orderBy': 'updated',
        },
        timeout=10,
    )
    if response.status_code != 200:
        logger.error("dashboard._sync_changed_events: api error | user_id=%s status=%s",
                     user.pk, response.status_code)
        return

    items = response.json().get('items', [])
    for item in items:
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
