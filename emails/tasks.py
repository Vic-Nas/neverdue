# emails/tasks.py
import logging
from celery import shared_task
from django.utils import timezone
from emails.models import ScanJob

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
            logger.error("emails._create_job: invalid source | user_id=%s source=%s", user_id, source)
            return None

        return ScanJob.objects.create(
            user_id=user_id,
            source=source,
            from_address=from_address,
            status=ScanJob.STATUS_QUEUED,
        )
    except Exception as exc:
        logger.error("emails._create_job: create failed | user_id=%s error=%s", user_id, exc)
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
        logger.error("emails._set_processing: update failed | job_id=%s error=%s", job.pk, exc)


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
        logger.error("emails._set_failed: update failed | job_id=%s error=%s", job.pk, exc)


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
        logger.error("emails._set_done: update failed | job_id=%s error=%s", job.pk, exc)


def _set_notes(job: 'ScanJob | None', notes: str) -> None:
    """Update a job's notes field. Silently ignores missing jobs."""
    if job is None:
        return
    try:
        from emails.models import ScanJob
        ScanJob.objects.filter(pk=job.pk).update(notes=notes[:255])
    except Exception as exc:
        logger.error("emails._set_notes: update failed | job_id=%s error=%s", job.pk, exc)


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


def _log_attempt(job: 'ScanJob | None', status: str, failure_reason: str = '') -> None:
    """
    Log a job execution attempt for metrics reporting.
    
    Called after every task completion (success or failure).
    Creates an immutable audit trail that survives job retry/success transitions.
    
    status: 'done' or 'failed'
    failure_reason: only used if status='failed'; one of ScanJob.REASON_* constants
    """
    if job is None:
        return
    try:
        from emails.models import JobAttemptLog
        JobAttemptLog.objects.create(
            job=job,
            status=status,
            failure_reason=failure_reason[:30] if failure_reason else '',
        )
    except Exception as exc:
        logger.error("emails._log_attempt: create failed | job_id=%s status=%s error=%s", job.pk, status, exc)


def _get_user_or_fail(user_id: int, job: 'ScanJob | None') -> 'User | None':
    """
    Fetch User by pk. On DoesNotExist, marks job failed and returns None.
    Caller must return immediately when None is returned.
    """
    from accounts.models import User
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("emails._get_user_or_fail: user not found | user_id=%s job_id=%s", user_id, job.pk if job else "?")
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature="User.DoesNotExist")
        _log_attempt(job, "failed", failure_reason=ScanJob.REASON_INTERNAL_ERROR)
        return None


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
    except Exception as exc:
        # Non-fatal — never let tracking failure break the pipeline.
        logger.error("emails.track_llm_usage: update failed | user_id=%s input_tokens=%s output_tokens=%s error=%s", user_id, input_tokens, output_tokens, exc)


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 5, 'countdown': 60}, acks_late=True)
def process_inbound_email(self, job_id: int, user_id: int, body: str, sender: str, message_id: str, attachments: list = None):
    """
    Process a single inbound email through the LLM pipeline.

    One email → one ScanJob, always. Job is created by webhook BEFORE task is queued.
    The pipeline (_save_events) decides the terminal status (done / needs_review).
    This task only sets processing and failed states on the existing job.

    Retry Configuration:
    - Retries up to 5 times on ANY exception
    - Initial delay: 60 seconds
    - Exponential backoff: 60s, 120s, 240s, 480s, 960s
    - acks_late=True: Task marked complete only after successful processing

    job_id: Pre-created ScanJob.pk (created by webhook before task is queued)
    attachments: list of [base64_string, media_type] or [base64_string, media_type, filename].
    """
    from accounts.models import User
    from dashboard.models import Event
    from llm.pipeline import process_email
    from emails.models import ScanJob
    import json

    # Retrieve the job created by webhook
    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_inbound_email: job not found | job_id=%s user_id=%s", job_id, user_id)
        return

    # Store task args for potential manual replay
    task_args = {
        'user_id': user_id,
        'body': body,
        'sender': sender,
        'message_id': message_id,
        'attachments': attachments,
    }
    try:
        job.task_args = json.dumps(task_args)
        job.save(update_fields=['task_args'])
    except Exception as exc:
        logger.error("emails.process_inbound_email: save task_args failed | job_id=%s user_id=%s error=%s", job_id, user_id, exc)

    user = _get_user_or_fail(user_id, job)
    if user is None:
        return

    # Duplicate guard: if this message_id already produced active events, skip.
    # This guard relies on source_email_id being preserved through reprocess — see pipeline.py.
    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        _set_notes(job, 'Email already processed — skipped.')
        _set_done(job)
        _log_attempt(job, 'done')
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
        _log_attempt(job, 'done')
    except Exception as exc:
        logger.error("emails.process_inbound_email: process_email failed | job_id=%s user_id=%s error=%s", job_id, user_id, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        _log_attempt(job, 'failed', failure_reason='internal_error')
        return


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 5, 'countdown': 60}, acks_late=True)
def process_uploaded_file(self, job_id: int, user_id: int, file_b64: str, media_type: str, context: str = '', filename: str = ''):
    """
    Process a file uploaded via the dashboard.

    Routes through process_email (empty body, single attachment) so the unified
    pipeline and filename-context feature are used consistently.

    Retry Configuration:
    - Retries up to 5 times on ANY exception
    - Initial delay: 60 seconds (exponential backoff)
    - acks_late=True: Task marked complete only after successful processing

    file_b64:  base64-encoded file contents.
    filename:  original filename — passed as context hint to the LLM.
    context:   optional user text, sent as the email body alongside the file.
    """
    from accounts.models import User
    from llm.pipeline import process_email
    import json

    # Retrieve the job created by dashboard view
    from emails.models import ScanJob
    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_uploaded_file: job not found | job_id=%s user_id=%s", job_id, user_id)
        return

    # Store task args for potential replay on retry
    task_args = {
        'user_id': user_id,
        'file_b64': file_b64,
        'media_type': media_type,
        'context': context,
        'filename': filename,
    }
    try:
        job.task_args = json.dumps(task_args)
        job.save(update_fields=['task_args'])
    except Exception as exc:
        logger.error("emails.process_uploaded_file: save task_args failed | job_id=%s user_id=%s error=%s", job_id, user_id, exc)

    user = _get_user_or_fail(user_id, job)
    if user is None:
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
        _log_attempt(job, 'done')
    except Exception as exc:
        logger.error("emails.process_uploaded_file: process_email failed | job_id=%s user_id=%s error=%s", job_id, user_id, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        _log_attempt(job, 'failed', failure_reason='internal_error')
        return


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 5, 'countdown': 60}, acks_late=True)
def reprocess_events(self, user_id: int, event_ids: list, prompt: str, job_pk: int = None):
    """
    Reprocess a needs_review job after the user supplies a correction prompt.

    Contract:
    - NEVER creates a new ScanJob. The original job is mutated.
    - Reads and serializes pending event data BEFORE deleting them.
    - Deletes the pending events only AFTER a successful LLM extraction.
    - Re-extracts using the user's prompt, preserving source_email_id.
    - The pipeline (_save_events) sets the terminal job status.

    Retry Configuration:
    - Retries up to 5 times on ANY exception
    - Initial delay: 60 seconds (exponential backoff)
    - acks_late=True: Task marked complete only after successful processing

    job_pk must be the pk of the original ScanJob the user is reviewing.
    If job_pk is missing (legacy call), logs an error and aborts — do not
    silently create a new job.
    """
    from accounts.models import User
    from dashboard.models import Event
    from emails.models import ScanJob
    from llm.pipeline import process_text

    if job_pk is None:
        logger.error("emails.reprocess_events: job_pk missing | user_id=%s", user_id)
        return

    try:
        job = ScanJob.objects.get(pk=job_pk, user_id=user_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.reprocess_events: job not found | job_pk=%s user_id=%s", job_pk, user_id)
        return

    user = _get_user_or_fail(user_id, job)
    if user is None:
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
        events_qs.delete()
        _set_done(job)
        return

    # Serialize event data into text the LLM can read
    blocks = [e.serialize_as_text() for e in events_list]
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
        # Terminal status (done / needs_review) is set by _save_events inside process_text.
    except Exception as exc:
        logger.error("emails.reprocess_events: process_text failed | job_pk=%s user_id=%s error=%s", job_pk, user_id, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        raise


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 5, 'countdown': 60}, acks_late=True)
def process_text_as_upload(self, job_id: int, user_id: int, text: str):
    """
    User-initiated re-extraction from event_prompt_edit or bulk reprocess.

    The user deleted one or more events from the dashboard and supplied a
    correction prompt. This creates a new upload job — it is NOT a fix of a
    needs_review job and does NOT reuse any existing job.

    Retry Configuration:
    - Retries up to 5 times on ANY exception
    - Initial delay: 60 seconds (exponential backoff)
    - acks_late=True: Task marked complete only after successful processing

    The assembled text already contains the original event data plus the
    user's instruction, built by the view before dispatch.
    """
    from accounts.models import User
    from llm.pipeline import process_text
    from emails.models import ScanJob

    # Retrieve the job created by dashboard view
    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_text_as_upload: job not found | job_id=%s user_id=%s", job_id, user_id)
        return

    user = _get_user_or_fail(user_id, job)
    if user is None:
        return

    _set_processing(job)
    try:
        created, notes = process_text(user, text, scan_job=job)
        if notes:
            _set_notes(job, notes)
        elif not created:
            _set_notes(job, 'No events found.')
        _log_attempt(job, 'done')
    except Exception as exc:
        logger.error("emails.process_text_as_upload: process_text failed | job_id=%s user_id=%s error=%s", job_id, user_id, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        _log_attempt(job, 'failed', failure_reason='internal_error')
        return


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
def reset_monthly_scans():
    """
    Snapshot current-month token usage into MonthlyUsage, then reset all
    monthly counters for users whose scan_reset_date is in a prior month.
    Scheduled via Celery Beat on the 1st of each month.

    Also re-enqueues failed jobs with reason=scan_limit so they are retried
    automatically once the quota resets.

    Retry Configuration:
    - Retries up to 3 times on transient exceptions
    - Initial delay: 300 seconds (5 minutes)
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

    # Retry scan_limit jobs now that quotas have reset.
    _retry_failed_jobs(reason=ScanJob.REASON_SCAN_LIMIT)


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 60})
def retry_jobs_after_plan_upgrade(user_id: int):
    """
    Re-enqueue all failed jobs for a user that were blocked by plan restrictions
    (scan_limit or pro_required). Called from the billing webhook after a
    successful subscription activation.

    Retry Configuration:
    - Retries up to 3 times on transient exceptions
    - Initial delay: 60 seconds
    """
    from emails.models import ScanJob

    jobs = ScanJob.objects.filter(
        user_id=user_id,
        status=ScanJob.STATUS_FAILED,
        failure_reason__in=[ScanJob.REASON_SCAN_LIMIT, ScanJob.REASON_PRO_REQUIRED],
    )
    _reenqueue_jobs(list(jobs))


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
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

    _reenqueue_jobs(stale_jobs)


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
        except Exception as exc:
            logger.error("emails._reenqueue_jobs: update failed | job_id=%s error=%s", job.pk, exc)

    return count


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 5, 'countdown': 60}, acks_late=True)
def process_queued_job(self, scan_job_id: int):
    """
    Process a single queued job by replaying its stored task arguments.
    Called after a job is reset to queued status (e.g., after plan upgrade or manual retry).
    
    Retry Configuration:
    - Retries up to 5 times on ANY exception
    - Initial delay: 60 seconds (exponential backoff)
    - acks_late=True: Task marked complete only after successful processing
    
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
        logger.error("emails.process_queued_job: job not found | job_id=%s", scan_job_id)
        return

    # Restore and validate task args
    try:
        task_args = json.loads(job.task_args) if job.task_args else {}
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("emails.process_queued_job: parse task_args failed | job_id=%s error=%s", scan_job_id, exc)
        _set_failed(job, reason='internal_error', signature='Invalid task_args JSON')
        return

    if not task_args:
        logger.error("emails.process_queued_job: task_args empty | job_id=%s", scan_job_id)
        _set_failed(job, reason='internal_error', signature='Missing task_args')
        return

    # Get user
    try:
        user = User.objects.get(pk=task_args.get('user_id'))
    except User.DoesNotExist:
        logger.error("emails.process_queued_job: user not found | job_id=%s user_id=%s", scan_job_id, task_args.get('user_id'))
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

        else:
            logger.error("emails.process_queued_job: unknown source | job_id=%s source=%s", scan_job_id, job.source)
            _set_failed(job, reason='internal_error', signature=f'Unknown job source: {job.source}')

    except Exception as exc:
        logger.error("emails.process_queued_job: process failed | job_id=%s user_id=%s error=%s", scan_job_id, job.user_id, exc, exc_info=True)
        _set_failed(job, reason='internal_error', signature=_make_signature(exc))
        _log_attempt(job, 'failed', failure_reason='internal_error')
        raise


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
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

    Retry Configuration:
    - Retries up to 3 times on transient exceptions
    - Initial delay: 300 seconds (5 minutes)
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

    # 3. Old completed jobs — done only, never failed.
    # Failed jobs are kept until the user or admin acts on them.
    job_cutoff = timezone.now() - timezone.timedelta(days=1)
    ScanJob.objects.filter(
        status=ScanJob.STATUS_DONE,
        updated_at__lt=job_cutoff,
    ).delete()


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 2, 'countdown': 300})
def cleanup_job_attempt_logs():
    """
    Delete job attempt logs older than 30 days.
    
    Keeps metrics queryable (failure rate by reason, etc.) for the last month
    while preventing unbounded growth of the attempt log table.
    
    Scheduled daily via Celery Beat.
    
    Retry Configuration:
    - Retries up to 2 times on transient exceptions
    - Initial delay: 300 seconds (5 minutes)
    """
    from emails.models import JobAttemptLog
    
    cutoff = timezone.now() - timezone.timedelta(days=30)
    JobAttemptLog.objects.filter(created_at__lt=cutoff).delete()
