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


@app.task
def patch_category_reminders(user_id: int, category_id: int) -> None:
    """Patch GCal reminders for events inheriting from this category."""
    from accounts.models import User
    from dashboard.models import Category
    from dashboard.gcal import patch_event
    from dashboard.writer import _resolve_reminders

    try:
        user = User.objects.get(pk=user_id)
        category = Category.objects.get(pk=category_id)
    except (User.DoesNotExist, Category.DoesNotExist) as exc:
        logger.error(
            "dashboard.patch_category_reminders: lookup failed | user_id=%s category_id=%s error=%s",
            user_id, category_id, exc,
        )
        return

    reminders = _resolve_reminders([], category)
    body = {'reminders': {'useDefault': False, 'overrides': reminders}}

    # Only patch events that inherit reminders (event.reminders is empty).
    events = category.events.filter(
        status='active', reminders=[],
    ).exclude(google_event_id='').exclude(google_event_id__isnull=True)

    count = 0
    for event in events:
        if patch_event(user, event.google_event_id, body):
            count += 1

    logger.info(
        "dashboard.patch_category_reminders: patched %s event(s) | user_id=%s category_id=%s",
        count, user_id, category_id,
    )


@app.task
def sync_event_to_gcal(event_id: int) -> None:
    """Push local event changes to Google Calendar."""
    from dashboard.models import Event
    from dashboard.gcal import update_event
    from dashboard.writer import build_gcal_body

    try:
        event = Event.objects.select_related('user', 'category').get(pk=event_id)
    except Event.DoesNotExist:
        logger.error("dashboard.sync_event_to_gcal: event not found | event_id=%s", event_id)
        return

    user = event.user
    if not user.save_to_gcal or not event.google_event_id:
        return

    body = build_gcal_body(event)
    if update_event(user, event.google_event_id, body):
        logger.info("dashboard.sync_event_to_gcal: updated | event_id=%s gcal_id=%s", event_id, event.google_event_id)
    else:
        logger.warning("dashboard.sync_event_to_gcal: failed | event_id=%s gcal_id=%s", event_id, event.google_event_id)