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