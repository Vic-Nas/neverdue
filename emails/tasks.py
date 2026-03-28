# emails/tasks.py
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal job helpers
# The task layer owns: queued → processing, and → failed on exception.
# The pipeline (_save_events) owns: → done and → needs_review.
# ---------------------------------------------------------------------------

def _create_job(user_id: int, source: str, from_address: str = '') -> 'ScanJob | None':
    """
    Create a ScanJob in queued state. Returns the job instance or None on failure.
    source must be 'email' or 'upload' — never 'reprocess'.
    """
    try:
        from emails.models import ScanJob
        from accounts.models import User
        if source not in (ScanJob.SOURCE_EMAIL, ScanJob.SOURCE_UPLOAD):
            logger.error("_create_job: invalid source=%r — must be 'email' or 'upload'", source)
            return None
        user = User.objects.get(pk=user_id)
        return ScanJob.objects.create(
            user=user,
            status=ScanJob.STATUS_QUEUED,
            source=source,
            from_address=from_address,
        )
    except Exception as exc:
        logger.warning("_create_job: failed for user=%s: %s", user_id, exc)
        return None


def _set_processing(job: 'ScanJob | None') -> None:
    """Transition a job to processing. Silently ignores missing jobs."""
    if job is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job.pk).update(
            status=ScanJob.STATUS_PROCESSING,
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.warning("_set_processing: failed pk=%s: %s", job.pk, exc)


def _set_failed(job: 'ScanJob | None') -> None:
    """Transition a job to failed. Silently ignores missing jobs."""
    if job is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job.pk).update(
            status=ScanJob.STATUS_FAILED,
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.warning("_set_failed: failed pk=%s: %s", job.pk, exc)


def _set_done(job: 'ScanJob | None') -> None:
    """
    Transition a job directly to done (no events created, not an error).
    Use only for early-exit paths (duplicate email, empty prompt).
    Never call this after the pipeline has run — _save_events owns that.
    """
    if job is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job.pk).update(
            status=ScanJob.STATUS_DONE,
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.warning("_set_done: failed pk=%s: %s", job.pk, exc)


def _set_notes(job: 'ScanJob | None', notes: str) -> None:
    """Update a job's notes field. Silently ignores missing jobs."""
    if job is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job.pk).update(notes=notes[:255])
    except Exception as exc:
        logger.warning("_set_notes: failed pk=%s: %s", job.pk, exc)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@shared_task
def track_llm_usage(user_id: int, input_tokens: int, output_tokens: int) -> None:
    """
    Atomically increment the current-month LLM token counters for a user.

    Called by pipeline.py after every successful Anthropic API response.
    Uses F() expressions so concurrent workers never race and overwrite each other.
    Fires async (delay) so the HTTP or Celery worker that triggered it is not blocked.
    """
    from django.db.models import F
    from accounts.models import User

    try:
        User.objects.filter(pk=user_id).update(
            monthly_input_tokens=F('monthly_input_tokens') + input_tokens,
            monthly_output_tokens=F('monthly_output_tokens') + output_tokens,
        )
        logger.debug(
            "track_llm_usage: user=%s +%s in +%s out",
            user_id, input_tokens, output_tokens,
        )
    except Exception as exc:
        # Non-fatal — never let tracking failure break the pipeline.
        logger.warning("track_llm_usage: failed user=%s: %s", user_id, exc)


@shared_task
def process_inbound_email(user_id: int, body: str, sender: str, message_id: str, attachments: list = None):
    """
    Process a single inbound email through the LLM pipeline.

    One email → one ScanJob, always.
    The pipeline (_save_events) decides the terminal status (done / needs_review).
    This task only sets processing and failed.

    attachments: list of [base64_string, media_type] or [base64_string, media_type, filename].
    """
    from accounts.models import User
    from dashboard.models import Event
    from llm.pipeline import process_email

    logger.info("TASK START user=%s message_id=%s body_len=%s", user_id, message_id, len(body) if body else 0)

    job = _create_job(user_id, source='email', from_address=sender or '')

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_inbound_email: User pk=%s not found — aborting", user_id)
        _set_failed(job)
        return

    # Duplicate guard: if this message_id already produced active events, skip.
    # This guard relies on source_email_id being preserved through reprocess — see pipeline.py.
    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        logger.info("process_inbound_email: duplicate message_id=%s for user=%s — skipping", message_id, user_id)
        _set_notes(job, 'Email already processed — skipped.')
        _set_done(job)
        return

    _set_processing(job)
    try:
        created, notes = process_email(
            user, body, attachments,
            sender=sender,
            source_email_id=message_id,
            scan_job=job,
        )
        if notes:
            _set_notes(job, notes)
        elif not created:
            _set_notes(job, 'No events found in this email.')
        logger.info("process_inbound_email: done user=%s events=%s", user_id, len(created))
    except Exception as exc:
        logger.error("process_inbound_email: failed user=%s: %s", user_id, exc, exc_info=True)
        _set_failed(job)
        raise


@shared_task
def process_uploaded_file(user_id: int, file_b64: str, media_type: str, context: str = '', filename: str = ''):
    """
    Process a file uploaded via the dashboard.

    Routes through process_email (empty body, single attachment) so the unified
    pipeline and filename-context feature are used consistently.

    file_b64:  base64-encoded file contents.
    filename:  original filename — passed as context hint to the LLM.
    context:   optional user text, sent as the email body alongside the file.
    """
    from accounts.models import User
    from llm.pipeline import process_email

    logger.info(
        "UPLOAD TASK START user=%s media_type=%s filename=%r context_len=%s",
        user_id, media_type, filename, len(context) if context else 0,
    )

    job = _create_job(user_id, source='upload')

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_uploaded_file: User pk=%s not found — aborting", user_id)
        _set_failed(job)
        return

    attachments = [[file_b64, media_type, filename]]
    body = context or ''

    _set_processing(job)
    try:
        created, notes = process_email(
            user, body, attachments,
            scan_job=job,
        )
        if notes:
            _set_notes(job, notes)
        elif not created:
            _set_notes(job, 'No events found in this file.')
        logger.info("UPLOAD TASK DONE user=%s events=%s", user_id, len(created))
    except Exception as exc:
        logger.error("process_uploaded_file: failed user=%s: %s", user_id, exc, exc_info=True)
        _set_failed(job)
        raise


@shared_task
def reprocess_events(user_id: int, event_ids: list, prompt: str, job_pk: int = None):
    """
    Reprocess a needs_review job after the user supplies a correction prompt.

    Contract:
    - NEVER creates a new ScanJob. The original job is mutated.
    - Reads source_email_id from the pending events BEFORE deleting them.
    - Deletes the pending events.
    - Re-extracts using the user's prompt, preserving source_email_id.
    - The pipeline (_save_events) sets the terminal job status.

    job_pk must be the pk of the original ScanJob the user is reviewing.
    If job_pk is missing (legacy call), logs an error and aborts — do not
    silently create a new job.
    """
    from accounts.models import User
    from dashboard.models import Event
    from emails.models import ScanJob
    from llm.pipeline import process_text

    logger.info("REPROCESS TASK START user=%s event_ids=%s job_pk=%s", user_id, event_ids, job_pk)

    if job_pk is None:
        logger.error(
            "reprocess_events: job_pk not provided for user=%s — aborting. "
            "The caller must pass the original job pk.",
            user_id,
        )
        return

    try:
        job = ScanJob.objects.get(pk=job_pk, user_id=user_id)
    except ScanJob.DoesNotExist:
        logger.error("reprocess_events: ScanJob pk=%s not found for user=%s — aborting", job_pk, user_id)
        return

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("reprocess_events: User pk=%s not found — aborting", user_id)
        _set_failed(job)
        return

    # Preserve event data AND source_email_id BEFORE deleting.
    # The LLM needs the original event data — the prompt alone is just a correction
    # instruction, not enough to reconstruct events from scratch.
    events_qs = Event.objects.filter(pk__in=event_ids, user=user).select_related('category')
    events_list = list(events_qs)

    source_email_id = next(
        (e.source_email_id for e in events_list if e.source_email_id),
        ''
    )

    # Serialize event data into text the LLM can read
    blocks = []
    for e in events_list:
        lines = [
            f"Title: {e.title}",
            f"Start: {e.start.isoformat()}",
            f"End: {e.end.isoformat()}",
        ]
        if e.description:
            lines.append(f"Notes: {e.description}")
        if e.recurrence_freq:
            lines.append(f"Recurrence: {e.recurrence_freq}")
            if e.recurrence_until:
                lines.append(f"Recurrence until: {e.recurrence_until}")
        if e.category:
            lines.append(f"Category: {e.category.name}")
        if e.pending_concern:
            lines.append(f"Previous concern: {e.pending_concern}")
        blocks.append("\n".join(lines))

    events_qs.delete()
    logger.info("reprocess_events: deleted %s pending event(s) for user=%s", len(events_list), user_id)

    if not prompt.strip():
        logger.info("reprocess_events: empty prompt — marking job=%s done", job_pk)
        _set_done(job)
        return

    full_text = "\n\n---\n\n".join(blocks) + f"\n\nUser instruction: {prompt}"

    _set_processing(job)
    try:
        created, _ = process_text(
            user,
            full_text,
            source_email_id=source_email_id,
            scan_job=job,
        )
        # Terminal status (done / needs_review) is set by _save_events inside process_text.
        logger.info("reprocess_events: created %s new event(s) for user=%s job=%s", len(created), user_id, job_pk)
    except Exception as exc:
        logger.error("reprocess_events: failed user=%s job=%s: %s", user_id, job_pk, exc, exc_info=True)
        _set_failed(job)
        raise


@shared_task
def process_text_as_upload(user_id: int, text: str):
    """
    User-initiated re-extraction from event_prompt_edit or bulk reprocess.

    The user deleted one or more events from the dashboard and supplied a
    correction prompt. This creates a new upload job — it is NOT a fix of a
    needs_review job and does NOT reuse any existing job.

    The assembled text already contains the original event data plus the
    user's instruction, built by the view before dispatch.
    """
    from accounts.models import User
    from llm.pipeline import process_text

    logger.info("MANUAL UPLOAD TASK START user=%s text_len=%s", user_id, len(text))

    job = _create_job(user_id, source='upload')

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_text_as_upload: User pk=%s not found — aborting", user_id)
        _set_failed(job)
        return

    _set_processing(job)
    try:
        created, notes = process_text(user, text, scan_job=job)
        if notes:
            _set_notes(job, notes)
        elif not created:
            _set_notes(job, 'No events found.')
        logger.info("MANUAL UPLOAD TASK DONE user=%s events=%s", user_id, len(created))
    except Exception as exc:
        logger.error("process_text_as_upload: failed user=%s: %s", user_id, exc, exc_info=True)
        _set_failed(job)
        raise


@shared_task
def reset_monthly_scans():
    """
    Snapshot current-month token usage into MonthlyUsage, then reset all
    monthly counters for users whose scan_reset_date is in a prior month.
    Scheduled via Celery Beat on the 1st of each month.
    """
    from accounts.models import MonthlyUsage, User

    today = timezone.now().date()
    # last day of the previous month = first day of this month minus one day
    last_month = today.replace(day=1) - timezone.timedelta(days=1)

    users_to_reset = User.objects.filter(scan_reset_date__month__lt=today.month)

    for user in users_to_reset:
        MonthlyUsage.objects.update_or_create(
            user=user,
            year=last_month.year,
            month=last_month.month,
            defaults={
                'input_tokens': user.monthly_input_tokens,
                'output_tokens': user.monthly_output_tokens,
                # Snapshot the pricing constants at reset time so historical
                # cost calculations remain accurate if rates change later.
                'input_cost_per_million': '3.0000',
                'output_cost_per_million': '15.0000',
            },
        )

    updated = users_to_reset.update(
        monthly_scans=0,
        monthly_input_tokens=0,
        monthly_output_tokens=0,
        scan_reset_date=today,
    )
    logger.info("reset_monthly_scans: reset %s user(s)", updated)


@shared_task
def cleanup_events():
    """
    Daily cleanup task:
      1. Delete expired pending events (pending_expires_at <= today).
         Pending events are never in GCal — no GCal cleanup needed.
      2. For users with auto_delete_past_events enabled, delete active events
         past their retention window, respecting GCal preferences.
      3. Delete done/failed ScanJobs older than 1 day.
    """
    from dashboard.models import Event
    from dashboard.gcal import delete_from_gcal
    from accounts.models import User
    from emails.models import ScanJob

    today = timezone.now().date()

    # 1. Expired pending events
    expired_pending = Event.objects.filter(status='pending', pending_expires_at__lte=today)
    count = expired_pending.count()
    expired_pending.delete()
    if count:
        logger.info("cleanup_events: deleted %s expired pending event(s)", count)

    # 2. Past active events for opted-in users
    for user in User.objects.filter(auto_delete_past_events=True):
        cutoff = timezone.now() - timezone.timedelta(days=user.past_event_retention_days)
        for event in Event.objects.filter(user=user, status='active', end__lt=cutoff):
            event._skip_gcal_delete = True
            if user.delete_from_gcal_on_cleanup and event.google_event_id:
                delete_from_gcal(user, event.google_event_id)
            event.delete()
            logger.info(
                "cleanup_events: deleted event_id=%s for user=%s (gcal_removed=%s)",
                event.pk, user.pk, user.delete_from_gcal_on_cleanup,
            )

    # 3. Old completed/failed jobs (needs_review jobs are kept — user hasn't acted yet)
    job_cutoff = timezone.now() - timezone.timedelta(days=1)
    terminal_statuses = [ScanJob.STATUS_DONE, ScanJob.STATUS_FAILED]
    deleted_jobs, _ = ScanJob.objects.filter(
        status__in=terminal_statuses,
        updated_at__lt=job_cutoff,
    ).delete()
    if deleted_jobs:
        logger.info("cleanup_events: deleted %s old ScanJob(s)", deleted_jobs)