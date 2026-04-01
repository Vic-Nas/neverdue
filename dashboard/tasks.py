# dashboard/tasks.py
import logging
from procrastinate.contrib.django import app

logger = logging.getLogger(__name__)


@app.task
def patch_category_colors(user_id: int, category_id: int) -> None:
    """
    Asynchronously patch GCal event colors for a category.
    Called after category color change to sync colors to Google Calendar.
    """
    from accounts.models import User
    from dashboard.models import Category
    from dashboard.gcal import patch_event_color

    try:
        user = User.objects.get(pk=user_id)
        category = Category.objects.get(pk=category_id)
    except (User.DoesNotExist, Category.DoesNotExist) as exc:
        logger.error(
            "dashboard.patch_category_colors: lookup failed | user_id=%s category_id=%s error=%s",
            user_id, category_id, exc,
        )
        return

    color_id = category.gcal_color_id
    if not color_id:
        return

    events = category.events.filter(
        status='active',
        color='',
    ).exclude(google_event_id='').exclude(google_event_id__isnull=True)

    count = 0
    for event in events:
        if patch_event_color(user, event.google_event_id, color_id):
            count += 1

    logger.info(
        "dashboard.patch_category_colors: patched %s event(s) | user_id=%s category_id=%s",
        count, user_id, category_id,
    )