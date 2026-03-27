# emails/tasks.py
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _create_job(user_id: int, source: str, summary: str = '') -> int | None:
    """Create a ScanJob in queued state. Returns job pk or None on failure."""
    try:
        from emails.models import ScanJob
        from accounts.models import User
        user = User.objects.get(pk=user_id)
        job = ScanJob.objects.create(
            user=user,
            status=ScanJob.STATUS_QUEUED,
            source=source,
            summary=summary[:255],
        )
        return job.pk
    except Exception as exc:
        logger.warning("_create_job: failed for user=%s: %s", user_id, exc)
        return None


def _set_job_status(job_pk: int | None, status: str) -> None:
    """Update a ScanJob's status. Silently ignores missing jobs."""
    if job_pk is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job_pk).update(status=status, updated_at=timezone.now())
    except Exception as exc:
        logger.warning("_set_job_status: failed pk=%s status=%s: %s", job_pk, status, exc)


@shared_task
def process_inbound_email(user_id: int, body: str, sender: str, message_id: str, attachments: list = None):
    """
    Process a single inbound email through the LLM pipeline.
    Body and attachments are sent together in a single LLM call.
    Runs asynchronously so the Resend webhook returns immediately.
    attachments: list of [base64_string, media_type] or [base64_string, media_type, filename] entries.
    """
    from accounts.models import User
    from llm.pipeline import process_email
    from dashboard.models import Event

    summary = f"From: {sender}" if sender else "Inbound email"
    job_pk = _create_job(user_id, source='email', summary=summary)

    logger.info("TASK START user=%s message_id=%s body_len=%s", user_id, message_id, len(body) if body else 0)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("User pk=%s not found — aborting task", user_id)
        _set_job_status(job_pk, 'failed')
        return

    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        logger.info("Duplicate message_id=%s for user=%s — skipping", message_id, user_id)
        _set_job_status(job_pk, 'done')
        return

    _set_job_status(job_pk, 'processing')
    try:
        process_email(user, body, attachments, sender=sender, source_email_id=message_id)
        _set_job_status(job_pk, 'done')
    except Exception as exc:
        logger.error("process_inbound_email: failed for user=%s: %s", user_id, exc)
        _set_job_status(job_pk, 'failed')
        raise


@shared_task
def process_uploaded_file(user_id: int, file_b64: str, media_type: str, context: str = '', filename: str = ''):
    """
    Process a file uploaded via the dashboard upload form.
    Routes through process_email (empty body, single attachment) so the unified
    pipeline and filename-context feature are used consistently.

    file_b64:  base64-encoded file contents
    filename:  original filename — passed as a context hint to the LLM when informative
    context:   optional user context string, sent as the email body
    """
    from accounts.models import User
    from llm.pipeline import process_email

    summary = f"File: {filename}" if filename else f"Upload ({media_type})"
    job_pk = _create_job(user_id, source='upload', summary=summary)

    logger.info("UPLOAD TASK START user=%s media_type=%s filename=%r context_len=%s",
                user_id, media_type, filename, len(context) if context else 0)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_uploaded_file: User pk=%s not found — aborting", user_id)
        _set_job_status(job_pk, 'failed')
        return

    # Single attachment tuple: [base64_string, media_type, filename]
    attachments = [[file_b64, media_type, filename]]

    # User context travels as the body so the LLM sees it alongside the file.
    body = context or ''

    _set_job_status(job_pk, 'processing')
    try:
        created = process_email(user, body, attachments)
        _set_job_status(job_pk, 'done')
        logger.info("UPLOAD TASK DONE user=%s events_created=%s", user_id, len(created))
    except Exception as exc:
        logger.error("process_uploaded_file: failed for user=%s: %s", user_id, exc)
        _set_job_status(job_pk, 'failed')
        raise


@shared_task
def reprocess_events(user_id: int, event_ids: list, prompt: str):
    """
    Delete selected events (and from GCal), then re-extract using the prompt text.
    GCal deletion is handled automatically by the pre_delete signal on Event.
    """
    from accounts.models import User
    from dashboard.models import Event
    from llm.pipeline import process_text

    snippet = (prompt[:80] + '…') if len(prompt) > 80 else prompt
    summary = f"Reprocess: {snippet}" if snippet else "Reprocess (no prompt)"
    job_pk = _create_job(user_id, source='reprocess', summary=summary)

    logger.info("REPROCESS TASK START user=%s event_ids=%s", user_id, event_ids)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("reprocess_events: User pk=%s not found — aborting", user_id)
        _set_job_status(job_pk, 'failed')
        return

    events = Event.objects.filter(pk__in=event_ids, user=user)
    deleted_count, _ = events.delete()
    logger.info("reprocess_events: deleted %s event(s) for user=%s", deleted_count, user_id)

    if prompt.strip():
        _set_job_status(job_pk, 'processing')
        try:
            created = process_text(user, prompt)
            _set_job_status(job_pk, 'done')
            logger.info("reprocess_events: created %s new event(s) for user=%s", len(created), user_id)
        except Exception as exc:
            logger.error("reprocess_events: failed for user=%s: %s", user_id, exc)
            _set_job_status(job_pk, 'failed')
            raise
    else:
        _set_job_status(job_pk, 'done')


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
    3. Delete completed (done/failed) ScanJobs older than 1 day.

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
    from emails.models import ScanJob

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
            event._skip_gcal_delete = True
            if user.delete_from_gcal_on_cleanup and event.google_event_id:
                delete_from_gcal(user, event.google_event_id)
            event.delete()
            logger.info("cleanup_events: deleted event_id=%s for user=%s (gcal_removed=%s)",
                        event.pk, user.pk, user.delete_from_gcal_on_cleanup)

    # 3. Delete done/failed ScanJobs older than 1 day
    job_cutoff = timezone.now() - timezone.timedelta(days=1)
    deleted_jobs, _ = ScanJob.objects.filter(
        status__in=[ScanJob.STATUS_DONE, ScanJob.STATUS_FAILED],
        updated_at__lt=job_cutoff,
    ).delete()
    if deleted_jobs:
        logger.info("cleanup_events: deleted %s old ScanJob(s)", deleted_jobs)
