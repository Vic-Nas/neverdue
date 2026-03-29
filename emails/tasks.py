# emails/tasks.py
import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers — job state transitions
# ---------------------------------------------------------------------------
# The task layer owns: queued → processing, and → failed on exception.
# The pipeline (_save_events) owns: → done and → needs_review.

def _create_job(user_id: int, source: str, from_address: str = '') -> 'ScanJob | None':
    """
    Create a ScanJob in queued state. Returns the job instance or None on failure.
    """
    try:
        from emails.models import ScanJob

        if source not in (ScanJob.SOURCE_EMAIL, ScanJob.SOURCE_UPLOAD):
            logger.error("_create_job: invalid source=%r for user=%s", source, user_id)
            return None

        return ScanJob.objects.create(
            user_id=user_id,
            source=source,
            from_address=from_address,
            status=ScanJob.STATUS_QUEUED,
        )
    except Exception as exc:
        logger.error("_create_job: failed for user=%s: %s", user_id, exc)
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


def _set_failed(job: 'ScanJob | None', reason: str = '', signature: str = '') -> None:
    """
    Transition a job to failed with a reason code and optional signature.

    reason:    one of ScanJob.REASON_* constants.
    signature: short exception identifier for internal_error grouping,
               e.g. 'AnthropicError: 529 overloaded'.
    """
    if job is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job.pk).update(
            status=ScanJob.STATUS_FAILED,
            failure_reason=reason[:30] if reason else '',
            failure_signature=signature[:255] if signature else '',
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.warning("_set_failed: failed pk=%s: %s", job.pk, exc)


def _set_done(job: 'ScanJob | None') -> None:
    """
    Transition a job to done.

    NOTE: This must ONLY be called for early-exit paths (duplicate guard,
    empty reprocess prompt). Terminal status for normal pipeline runs is
    set by _save_events in pipeline.py — not here.
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


def _make_signature(exc: Exception) -> str:
    """
    Build a short failure signature from an exception for grouping in the admin.
    e.g. 'RateLimitError: 429 rate limit exceeded'
    Truncated to 255 chars.
    """
    name = type(exc).__name__
    msg = str(exc)
    # Keep only the first line to avoid huge tracebacks
    first_line = msg.splitlines()[0] if msg else ''
    sig = f"{name}: {first_line}" if first_line else name
    return sig[:255]


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
    import json

    logger.info("TASK START user=%s message_id=%s body_len=%s", user_id, message_id, len(body) if body else 0)

    # Store task args for potential replay on retry
    task_args = {
        'user_id': user_id,
        'body': body,
        'sender': sender,
        'message_id': message_id,
        'attachments': attachments,
    }
    
    job = _create_job(user_id, source='email', from_address=sender or '')
    
    if job:
        try:
            import json
            job.task_args = json.dumps(task_args)
            job.save(update_fields=['task_args'])
        except Exception as exc:
            logger.warning("process_inbound_email: failed to store task_args: %s", exc)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_inbound_email: User pk=%s not found — aborting", user_id)
        _set_failed(job, reason='internal_error', signature='User.DoesNotExist')
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
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
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
    import json

    logger.info(
        "UPLOAD TASK START user=%s media_type=%s filename=%r context_len=%s",
        user_id, media_type, filename, len(context) if context else 0,
    )

    # Store task args for potential replay on retry
    task_args = {
        'user_id': user_id,
        'file_b64': file_b64,
        'media_type': media_type,
        'context': context,
        'filename': filename,
    }

    job = _create_job(user_id, source='upload')
    
    if job:
        try:
            job.task_args = json.dumps(task_args)
            job.save(update_fields=['task_args'])
        except Exception as exc:
            logger.warning("process_uploaded_file: failed to store task_args: %s", exc)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("process_uploaded_file: User pk=%s not found — aborting", user_id)
        _set_failed(job, reason='internal_error', signature='User.DoesNotExist')
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
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        raise


@shared_task
def reprocess_events(user_id: int, event_ids: list, prompt: str, job_pk: int = None):
    """
    Reprocess a needs_review job after the user supplies a correction prompt.

    Contract:
    - NEVER creates a new ScanJob. The original job is mutated.
    - Reads and serializes pending event data BEFORE deleting them.
    - Deletes the pending events only AFTER a successful LLM extraction.
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
        _set_failed(job, reason='internal_error', signature='User.DoesNotExist')
        return

    # Preserve event data AND source_email_id BEFORE any deletion.
    # Events are only deleted after a successful LLM call — if the LLM fails,
    # the pending events remain intact and the job stays recoverable.
    events_qs = Event.objects.filter(pk__in=event_ids, user=user, status='pending').select_related('category')
    events_list = list(events_qs)

    source_email_id = next(
        (e.source_email_id for e in events_list if e.source_email_id),
        ''
    )

    if not prompt.strip():
        logger.info("reprocess_events: empty prompt — marking job=%s done", job_pk)
        events_qs.delete()
        _set_done(job)
        return

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

    full_text = "\n\n---\n\n".join(blocks) + f"\n\nUser instruction: {prompt}"

    _set_processing(job)
    try:
        created, _ = process_text(
            user,
            full_text,
            source_email_id=source_email_id,
            scan_job=job,
        )
        # LLM succeeded — now safe to delete the old pending events.
        events_qs.delete()
        logger.info("reprocess_events: deleted %s pending event(s), created %s new event(s) for user=%s job=%s",
                    len(events_list), len(created), user_id, job_pk)
        # Terminal status (done / needs_review) is set by _save_events inside process_text.
    except Exception as exc:
        logger.error("reprocess_events: failed user=%s job=%s: %s", user_id, job_pk, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
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
        _set_failed(job, reason='internal_error', signature='User.DoesNotExist')
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
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        raise


@shared_task
def reset_monthly_scans():
    """
    Snapshot current-month token usage into MonthlyUsage, then reset all
    monthly counters for users whose scan_reset_date is in a prior month.
    Scheduled via Celery Beat on the 1st of each month.

    Also re-enqueues failed jobs with reason=scan_limit so they are retried
    automatically once the quota resets.
    """
    from accounts.models import MonthlyUsage, User
    from emails.models import ScanJob

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

    # Retry scan_limit jobs now that quotas have reset.
    _retry_failed_jobs(reason=ScanJob.REASON_SCAN_LIMIT)


@shared_task
def retry_jobs_after_plan_upgrade(user_id: int):
    """
    Re-enqueue all failed jobs for a user that were blocked by plan restrictions
    (scan_limit or pro_required). Called from the billing webhook after a
    successful subscription activation.
    """
    from emails.models import ScanJob

    jobs = ScanJob.objects.filter(
        user_id=user_id,
        status=ScanJob.STATUS_FAILED,
        failure_reason__in=[ScanJob.REASON_SCAN_LIMIT, ScanJob.REASON_PRO_REQUIRED],
    )
    count = _reenqueue_jobs(list(jobs))
    logger.info("retry_jobs_after_plan_upgrade: re-enqueued %s job(s) for user=%s", count, user_id)


@shared_task
def recover_stale_jobs():
    """
    Reset jobs stuck in 'processing' for longer than 10 minutes back to 'queued'
    and re-enqueue them.

    A job gets stuck at processing when a worker crashes mid-task before it can
    set the terminal status. Without this task, those jobs would stay at
    processing forever and never appear as failed to the user.

    Scheduled every 10 minutes via Celery Beat.
    """
    from emails.models import ScanJob

    cutoff = timezone.now() - timezone.timedelta(minutes=10)
    stale_jobs = list(
        ScanJob.objects.filter(status=ScanJob.STATUS_PROCESSING, updated_at__lt=cutoff)
    )
    if not stale_jobs:
        return

    count = _reenqueue_jobs(stale_jobs)
    logger.warning("recover_stale_jobs: recovered %s stale job(s)", count)


def _retry_failed_jobs(reason: str) -> int:
    """
    Re-enqueue all failed jobs with a given failure_reason across all users.
    Returns the number of jobs re-enqueued.
    """
    from emails.models import ScanJob

    jobs = list(ScanJob.objects.filter(status=ScanJob.STATUS_FAILED, failure_reason=reason))
    return _reenqueue_jobs(jobs)


def _reenqueue_jobs(jobs: list) -> int:
    """
    Reset a list of ScanJob instances to 'queued' and dispatch a task to process them.
    Returns the number successfully re-enqueued.

    Uses stored task_args to replay the original task.
    """
    from emails.models import ScanJob

    count = 0
    for job in jobs:
        try:
            ScanJob.objects.filter(pk=job.pk).update(
                status=ScanJob.STATUS_QUEUED,
                failure_reason='',
                failure_signature='',
                notes='Queued for retry.',
                updated_at=timezone.now(),
            )
            
            # Dispatch task to process this queued job with stored args
            process_queued_job.delay(job.pk)
            count += 1
            logger.info("_reenqueue_jobs: reset job=%s to queued and dispatched process task", job.pk)
        except Exception as exc:
            logger.error("_reenqueue_jobs: failed for job=%s: %s", job.pk, exc)

    return count


@shared_task
def process_queued_job(scan_job_id: int):
    """
    Process a single queued job by replaying its stored task arguments.
    Called after a job is reset to queued status (e.g., after plan upgrade or manual retry).
    
    IMPORTANT: This task calls the pipeline directly with the EXISTING job object,
    instead of re-dispatching the original task. This prevents creating duplicate jobs.
    """
    import json
    from emails.models import ScanJob
    from accounts.models import User
    from dashboard.models import Event
    from llm.pipeline import process_email

    try:
        job = ScanJob.objects.get(pk=scan_job_id)
    except ScanJob.DoesNotExist:
        logger.warning("process_queued_job: job=%s not found", scan_job_id)
        return

    logger.info("process_queued_job: processing job=%s source=%s user=%s", scan_job_id, job.source, job.user_id)

    # Restore and validate task args
    try:
        task_args = json.loads(job.task_args) if job.task_args else {}
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("process_queued_job: failed to decode task_args for job=%s: %s", scan_job_id, exc)
        _set_failed(job, reason='internal_error', signature='Invalid task_args JSON')
        return

    if not task_args:
        logger.error("process_queued_job: no task_args for job=%s — cannot replay", scan_job_id)
        _set_failed(job, reason='internal_error', signature='Missing task_args')
        return

    # Get user
    try:
        user = User.objects.get(pk=task_args.get('user_id'))
    except User.DoesNotExist:
        logger.warning("process_queued_job: User pk=%s not found", task_args.get('user_id'))
        _set_failed(job, reason='internal_error', signature='User.DoesNotExist')
        return

    # Update job to processing (same state as original tasks do)
    _set_processing(job)

    try:
        if job.source == ScanJob.SOURCE_EMAIL:
            # Call pipeline directly with stored email args, reusing the EXISTING job
            message_id = task_args.get('message_id')
            sender = task_args.get('sender')
            body = task_args.get('body', '')
            attachments = task_args.get('attachments') or []

            # Duplicate guard: if this message_id already produced active events, skip.
            if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
                logger.info("process_queued_job: duplicate message_id=%s for user=%s — skipping", message_id, user.pk)
                _set_notes(job, 'Email already processed — skipped.')
                _set_done(job)
                return

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
            logger.info("process_queued_job: email done job=%s user=%s events=%s", scan_job_id, user.pk, len(created))

        elif job.source == ScanJob.SOURCE_UPLOAD:
            # Call pipeline directly with stored upload args, reusing the EXISTING job
            file_b64 = task_args.get('file_b64', '')
            media_type = task_args.get('media_type', '')
            context = task_args.get('context', '')
            filename = task_args.get('filename', '')
            
            attachments = [[file_b64, media_type, filename]]
            body = context or ''

            created, notes = process_email(
                user, body, attachments,
                scan_job=job,
            )
            if notes:
                _set_notes(job, notes)
            elif not created:
                _set_notes(job, 'No events found in this file.')
            logger.info("process_queued_job: upload done job=%s user=%s events=%s", scan_job_id, user.pk, len(created))

        else:
            logger.error("process_queued_job: unknown source=%r for job=%s", job.source, scan_job_id)
            _set_failed(job, reason='internal_error', signature=f'Unknown job source: {job.source}')

    except Exception as exc:
        logger.error("process_queued_job: failed to process job=%s: %s", scan_job_id, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))


@shared_task
def cleanup_events():
    """
    Daily cleanup task:
      1. Delete expired pending events (pending_expires_at <= today).
         Pending events are never in GCal — no GCal cleanup needed.
      2. For users with auto_delete_past_events enabled, delete active events
         past their retention window, respecting GCal preferences.
      3. Delete done ScanJobs older than 1 day.
         Failed jobs are NOT deleted — they stay visible until the user or
         admin dismisses them, and are retried automatically where possible.
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

    # 3. Old completed jobs — done only, never failed.
    # Failed jobs are kept until the user or admin acts on them.
    job_cutoff = timezone.now() - timezone.timedelta(days=1)
    deleted_jobs, _ = ScanJob.objects.filter(
        status=ScanJob.STATUS_DONE,
        updated_at__lt=job_cutoff,
    ).delete()
    if deleted_jobs:
        logger.info("cleanup_events: deleted %s old done ScanJob(s)", deleted_jobs)
