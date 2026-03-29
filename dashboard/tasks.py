#  dashboard/tasks.py
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def patch_category_colors(user_id: int, category_id: int) -> None:
    """
    Asynchronously patch GCal event colors for a category.
    Called after category color change to sync colors to Google Calendar.
    Runs in Celery worker so the HTTP view returns immediately.
    """
    from accounts.models import User
    from dashboard.models import Category
    from dashboard.gcal import patch_event_color

    try:
        user = User.objects.get(pk=user_id)
        category = Category.objects.get(pk=category_id)
    except (User.DoesNotExist, Category.DoesNotExist) as exc:
        logger.error("dashboard.patch_category_colors: lookup failed | user_id=%s category_id=%s error=%s",
                     user_id, category_id, exc)
        return

    color_id = category.gcal_color_id
    if not color_id:
        return

    # Find events to patch: active, no local color, has google_event_id
    events = category.events.filter(
        status='active',
        color='',
    ).exclude(google_event_id='').exclude(google_event_id__isnull=True)

    count = 0
    for event in events:
        if patch_event_color(user, event.google_event_id, color_id):
            count += 1
