# emails/tasks.py
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def process_inbound_email(user_id: int, body: str, sender: str, message_id: str, attachments: list = None):
    """
    Process a single inbound email through the LLM pipeline.
    Processes body text and each attachment as separate scans.
    Runs asynchronously so the Resend webhook returns immediately.
    attachments: list of [base64_string, media_type] pairs
    """
    from accounts.models import User
    from llm.pipeline import process_text, process_file
    from dashboard.models import Event
    import base64

    logger.info("TASK START user=%s message_id=%s body_len=%s", user_id, message_id, len(body) if body else 0)

    if settings.DEBUG:
        logger.debug(
            "[DEBUG] process_inbound_email called | user_id=%s | message_id=%s | sender=%s | body_len=%s | attachments=%s",
            user_id,
            message_id,
            sender,
            len(body) if body else 0,
            len(attachments) if attachments else 0,
        )
        if body:
            logger.debug("[DEBUG] Body preview: %r", body[:300])
        else:
            logger.debug("[DEBUG] Body is empty or None — process_text will be SKIPPED")

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("User pk=%s not found — aborting task", user_id)
        return

    # Skip entire email if already processed (dedup by message_id)
    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        logger.info("Duplicate message_id=%s for user=%s — skipping", message_id, user_id)
        return

    # Process body text
    if body:
        if settings.DEBUG:
            logger.debug("[DEBUG] Calling process_text for user=%s", user_id)
        process_text(user, body, sender=sender, source_email_id=message_id)
    else:
        logger.warning(
            "Body is empty for message_id=%s user=%s sender=%s — process_text skipped. "
            "Check webhook payload extraction.",
            message_id, user_id, sender,
        )

    # Process each attachment — counts as a scan each
    # Attachments are only processed for Pro users (process_file checks this)
    if attachments:
        for idx, (b64_content, media_type) in enumerate(attachments):
            if settings.DEBUG:
                logger.debug("[DEBUG] Processing attachment %s/%s | media_type=%s", idx + 1, len(attachments), media_type)
            try:
                file_bytes = base64.b64decode(b64_content)
                process_file(user, file_bytes, media_type)
            except Exception as exc:
                logger.warning("Attachment %s failed for message_id=%s: %s", idx, message_id, exc)
                continue
    elif settings.DEBUG:
        logger.debug("[DEBUG] No attachments to process")


@shared_task
def process_uploaded_file(user_id: int, file_b64: str, media_type: str, context: str = ''):
    """
    Process a file uploaded via the dashboard upload form.
    Runs asynchronously so the upload view returns immediately.
    file_b64: base64-encoded file contents
    """
    import base64
    from accounts.models import User
    from llm.pipeline import process_file

    logger.info("UPLOAD TASK START user=%s media_type=%s context_len=%s",
                user_id, media_type, len(context) if context else 0)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_uploaded_file: User pk=%s not found — aborting", user_id)
        return

    try:
        file_bytes = base64.b64decode(file_b64)
    except Exception as exc:
        logger.error("process_uploaded_file: base64 decode failed for user=%s: %s", user_id, exc)
        return

    created = process_file(user, file_bytes, media_type, context=context)
    logger.info("UPLOAD TASK DONE user=%s events_created=%s", user_id, len(created))


@shared_task
def reprocess_events(user_id: int, event_ids: list, prompt: str):
    """
    Delete selected events (and from GCal), then re-extract using the prompt text.
    GCal deletion is handled automatically by the pre_delete signal on Event.
    """
    from accounts.models import User
    from dashboard.models import Event
    from llm.pipeline import process_text

    logger.info("REPROCESS TASK START user=%s event_ids=%s", user_id, event_ids)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("reprocess_events: User pk=%s not found — aborting", user_id)
        return

    events = Event.objects.filter(pk__in=event_ids, user=user)
    deleted_count, _ = events.delete()  # signal fires per instance, handles GCal
    logger.info("reprocess_events: deleted %s event(s) for user=%s", deleted_count, user_id)

    if prompt.strip():
        created = process_text(user, prompt)
        logger.info("reprocess_events: created %s new event(s) for user=%s", len(created), user_id)


@shared_task
def reset_monthly_scans():
    """
    Reset monthly scan counters for all users at the start of each month.
    Scheduled via Celery Beat on the 1st of each month.
    """
    from accounts.models import User

    today = timezone.now().date()
    updated = User.objects.filter(
        scan_reset_date__month__lt=today.month
    ).update(monthly_scans=0, scan_reset_date=today)
    logger.info("reset_monthly_scans: reset %s user(s)", updated)


@shared_task
def cleanup_events():
    """
    Daily cleanup task:
    1. Delete expired pending events (pending_expires_at <= today).
    2. For users with auto_delete_past_events enabled, delete active events
       whose end date is older than their retention setting.

    Pending events are never in GCal — no cleanup needed there.

    For past active events: respects delete_from_gcal_on_cleanup preference.
    - If True:  call delete_from_gcal() directly, then delete the row.
                _skip_gcal_delete=True is set so the signal doesn't double-delete.
    - If False: set _skip_gcal_delete=True so the signal skips GCal,
                then delete the row (leaves event in GCal as user intended).
    """
    from dashboard.models import Event
    from dashboard.gcal import delete_from_gcal
    from accounts.models import User

    today = timezone.now().date()

    # 1. Delete expired pending events (never in GCal, signal skips them automatically)
    expired_pending = Event.objects.filter(
        status='pending',
        pending_expires_at__lte=today,
    )
    count = expired_pending.count()
    expired_pending.delete()
    if count:
        logger.info("cleanup_events: deleted %s expired pending event(s)", count)

    # 2. Auto-delete past active events for opted-in users
    users = User.objects.filter(auto_delete_past_events=True)
    for user in users:
        cutoff = timezone.now() - timezone.timedelta(days=user.past_event_retention_days)
        old_events = Event.objects.filter(user=user, status='active', end__lt=cutoff)

        for event in old_events:
            # Mark the instance so the pre_delete signal always skips GCal —
            # we handle it ourselves here to respect the preference.
            event._skip_gcal_delete = True

            if user.delete_from_gcal_on_cleanup and event.google_event_id:
                delete_from_gcal(user, event.google_event_id)

            event.delete()
            logger.info("cleanup_events: deleted event_id=%s for user=%s (gcal_removed=%s)",
                        event.pk, user.pk, user.delete_from_gcal_on_cleanup)
