# dashboard/gcal/watch.py
import logging
import uuid
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.urls import reverse
from googleapiclient.errors import HttpError

from .client import _service

logger = logging.getLogger(__name__)


def stop_gcal_watch(user, svc) -> None:
    if not user.gcal_channel_id or not user.gcal_channel_resource_id:
        return
    try:
        svc.channels().stop(body={
            'id': user.gcal_channel_id,
            'resourceId': user.gcal_channel_resource_id,
        }).execute()
    except HttpError as exc:
        if exc.resp.status != 404:
            logger.warning("dashboard.stop_gcal_watch: api error | status=%s user=%s", exc.resp.status, user.pk)
    except Exception as exc:
        logger.warning("dashboard.stop_gcal_watch: failed | user=%s error=%s", user.pk, exc)


def register_gcal_watch(user) -> bool:
    try:
        svc = _service(user)
    except Exception as exc:
        logger.warning("register_gcal_watch: token failed user=%s: %s", user.pk, exc)
        return False

    stop_gcal_watch(user, svc)

    channel_id = str(uuid.uuid4())
    webhook_path = reverse('dashboard:gcal_webhook')
    try:
        data = svc.events().watch(
            calendarId='primary',
            body={
                'id': channel_id,
                'type': 'web_hook',
                'address': f'https://{settings.DOMAIN}{webhook_path}',
            },
        ).execute()

        expiration_ms = int(data.get('expiration', 0))
        expiration_dt = datetime.fromtimestamp(expiration_ms / 1000, tz=dt_timezone.utc)

        user.gcal_channel_id = channel_id
        user.gcal_channel_resource_id = data.get('resourceId', '')
        user.gcal_channel_expiration = expiration_dt
        user.save(update_fields=['gcal_channel_id', 'gcal_channel_resource_id', 'gcal_channel_expiration'])
        return True
    except Exception as exc:
        logger.error("dashboard.register_gcal_watch: failed | user=%s error=%s", user.pk, exc)
        return False
