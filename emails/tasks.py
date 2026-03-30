# emails/tasks.py
import json
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from emails.models import ScanJob, JobAttemptLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job state helpers
#
# State ownership:
#   queued → processing          _set_processing  (task layer)
#   → done / needs_review        _set_terminal    (task layer, from ProcessingOutcome)
#   → failed                     _set_failed      (task layer, exception path only)
#
# You can read this file to trace every state transition a job can take.
# pipeline.py never writes job state — it returns ProcessingOutcome.
# ---------------------------------------------------------------------------

def _set_processing(job: 'ScanJob | None') -> None:
    """Transition job to processing."""
    if job is None:
        return
    try:
        ScanJob.objects.filter(pk=job.pk).update(
            status=ScanJob.STATUS_PROCESSING,
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.error("emails._set_processing: update failed | job_id=%s error=%s", job.pk, exc)


def _set_terminal(job: 'ScanJob | None', outcome: 'ProcessingOutcome') -> None:
    """
    Write terminal job state from a ProcessingOutcome.
    Single place for done / needs_review / failed writes.
    """
    if job is None:
        return
    try:
        ScanJob.objects.filter(pk=job.pk).update(
            status=outcome.status,
            failure_reason=outcome.failure_reason[:30] if outcome.failure_reason else '',
            failure_signature=outcome.failure_signature[:255] if outcome.failure_signature else '',
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.error("emails._set_terminal: update failed | job_id=%s error=%s", job.pk, exc)


def _set_failed(job: 'ScanJob | None', reason: str = '', signature: str = '') -> None:
    """
    Transition job to failed. Used only on the exception path in tasks,
    where no ProcessingOutcome exists. All other failure paths go through
    _set_terminal with a ProcessingOutcome from pipeline.
    """
    if job is None:
        return
    try:
        ScanJob.objects.filter(pk=job.pk).update(
            status=ScanJob.STATUS_FAILED,
            failure_reason=reason[:30] if reason else '',
            failure_signature=signature[:255] if signature else '',
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.error("emails._set_failed: update failed | job_id=%s error=%s", job.pk, exc)


def _set_notes(job: 'ScanJob | None', notes: str) -> None:
    if job is None:
        return
    try:
        ScanJob.objects.filter(pk=job.pk).update(notes=notes[:255])
    except Exception as exc:
        logger.error("emails._set_notes: update failed | job_id=%s error=%s", job.pk, exc)


def _log_attempt(job: 'ScanJob | None', outcome_status: str, failure_reason: str = '') -> None:
    """
    Append an immutable audit record for this processing attempt.
    Survives retry and success transitions — used for accurate metrics.

    outcome_status: the ProcessingOutcome.status value ('done', 'needs_review', 'failed').
    'needs_review' is a successful processing attempt — logged as 'done'.
    """
    if job is None:
        return
    log_status = 'done' if outcome_status in ('done', 'needs_review') else 'failed'
    try:
        JobAttemptLog.objects.create(
            job=job,
            status=log_status,
            failure_reason=failure_reason[:30] if failure_reason else '',
        )
    except Exception as exc:
        logger.error(
            "emails._log_attempt: create failed | job_id=%s status=%s error=%s",
            job.pk, log_status, exc,
        )


def _make_signature(exc: Exception) -> str:
    """Build a short failure signature from an exception for admin grouping."""
    name = type(exc).__name__
    first_line = str(exc).splitlines()[0] if str(exc) else ''
    sig = f"{name}: {first_line}" if first_line else name
    return sig[:255]


def _get_user_or_fail(user_id: int, job: 'ScanJob | None'):
    """
    Fetch User by pk. On DoesNotExist, marks job failed and returns None.
    Caller must return immediately when None is returned.
    """
    from accounts.models import User
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error(
            "emails._get_user_or_fail: user not found | user_id=%s job_id=%s",
            user_id, job.pk if job else '?',
        )
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature='User.DoesNotExist')
        _log_attempt(job, 'failed', ScanJob.REASON_INTERNAL_ERROR)
        return None


# ---------------------------------------------------------------------------
# Retry dispatcher
# ---------------------------------------------------------------------------

def _dispatch_job(job: ScanJob) -> None:
    """
    Re-dispatch a job to its original task using stored task_args.

    Called by _reenqueue_jobs after a job is reset to queued.
    Reads job.source and job.task_args — no new job is created.
    """
    try:
        args = json.loads(job.task_args or '{}')
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("emails._dispatch_job: parse task_args failed | job_id=%s error=%s", job.pk, exc)
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature='Invalid task_args JSON')
        return

    if not args:
        logger.error("emails._dispatch_job: task_args empty | job_id=%s", job.pk)
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature='Missing task_args')
        return

    if job.source == ScanJob.SOURCE_EMAIL:
        process_inbound_email.apply_async(
            kwargs={
                'job_id':      job.pk,
                'user_id':     args['user_id'],
                'email_id':    args['email_id'],
                'sender':      args.get('sender', ''),
                'message_id':  args.get('message_id', ''),
            },
            countdown=30,
        )
    elif job.source == ScanJob.SOURCE_UPLOAD:
        # 'text' key means it came from event_prompt_edit / events_bulk_action.
        if 'text' in args:
            process_text_as_upload.apply_async(
                kwargs={
                    'job_id':   job.pk,
                    'user_id':  args['user_id'],
                    'text':     args['text'],
                },
                countdown=30,
            )
        else:
            process_uploaded_file.apply_async(
                kwargs={
                    'job_id':     job.pk,
                    'user_id':    args['user_id'],
                    'file_b64':   args.get('file_b64', ''),
                    'media_type': args.get('media_type', ''),
                    'context':    args.get('context', ''),
                    'filename':   args.get('filename', ''),
                },
                countdown=30,
            )
    else:
        logger.error(
            "emails._dispatch_job: unknown source | job_id=%s source=%s",
            job.pk, job.source,
        )
        _set_failed(
            job,
            reason=ScanJob.REASON_INTERNAL_ERROR,
            signature=f'Unknown source: {job.source}',
        )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@shared_task
def track_llm_usage(user_id: int, input_tokens: int, output_tokens: int) -> None:
    """
    Atomically increment current-month LLM token counters for a user.
    Fired async by pipeline after every Anthropic API response.
    Uses F() so concurrent workers never overwrite each other.
    """
    from django.db.models import F
    from accounts.models import User
    try:
        User.objects.filter(pk=user_id).update(
            monthly_input_tokens=F('monthly_input_tokens') + input_tokens,
            monthly_output_tokens=F('monthly_output_tokens') + output_tokens,
        )
    except Exception as exc:
        logger.error(
            "emails.track_llm_usage: update failed | user_id=%s error=%s",
            user_id, exc,
        )


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 5, 'countdown': 60},
    acks_late=True,
)
def process_inbound_email(self, job_id: int, user_id: int, email_id: str, sender: str, message_id: str):
    """
    Process a single inbound email through the LLM pipeline.

    Flow (all I/O happens here — the webhook only stored metadata):
      1. Load the pre-created ScanJob
      2. Duplicate guard on message_id
      3. Fetch full email from Resend (body + attachment bytes) — retryable
      4. Extract text and attachments
      5. Run process_email → ProcessingOutcome
      6. Write all job state from outcome

    On retry: Resend keeps emails for 24 h. Re-fetching is safe and correct.
    Retry: up to 5x, 60 s initial, exponential backoff via autoretry_for.
    acks_late: task acked only after successful return (no ack on worker crash).
    """
    from dashboard.models import Event
    from llm.pipeline import process_email, ProcessingOutcome
    from emails.webhook import fetch_full_email, extract_email_text, extract_attachments

    if settings.DEBUG:
        logger.debug(
            "emails.process_inbound_email: start | job_id=%s user_id=%s email_id=%s sender=%s",
            job_id, user_id, email_id, sender,
        )

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error(
            "emails.process_inbound_email: job not found | job_id=%s user_id=%s",
            job_id, user_id,
        )
        return

    user = _get_user_or_fail(user_id, job)
    if user is None:
        return

    # Duplicate guard: if this message_id already produced active events, skip.
    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        if settings.DEBUG:
            logger.debug(
                "emails.process_inbound_email: duplicate skipped | job_id=%s message_id=%s",
                job_id, message_id,
            )
        _set_notes(job, 'Email already processed — skipped.')
        _set_terminal(job, ProcessingOutcome(status='done'))
        _log_attempt(job, 'done')
        return

    # Fetch full email — if Resend returns nothing, raise so autoretry kicks in.
    if settings.DEBUG:
        logger.debug(
            "emails.process_inbound_email: fetching email | job_id=%s email_id=%s",
            job_id, email_id,
        )
    full_email = fetch_full_email(email_id)
    if not full_email:
        raise RuntimeError(
            f"emails.process_inbound_email: fetch returned empty | job_id={job_id} email_id={email_id}"
        )

    body = extract_email_text(full_email)
    attachments = extract_attachments(full_email)

    if settings.DEBUG:
        logger.debug(
            "emails.process_inbound_email: extracted | job_id=%s body_len=%s attachments=%s",
            job_id, len(body), len(attachments),
        )

    _set_processing(job)
    try:
        outcome = process_email(
            user, body, attachments,
            sender=sender,
            source_email_id=message_id,
        )
        if settings.DEBUG:
            logger.debug(
                "emails.process_inbound_email: outcome | job_id=%s status=%s created=%s notes=%r",
                job_id, outcome.status, len(outcome.created), outcome.notes,
            )
        if not outcome.notes and not outcome.created and outcome.status == 'done':
            outcome.notes = 'No events found in this email.'
        _set_terminal(job, outcome)
        _set_notes(job, outcome.notes)
        _log_attempt(job, outcome.status, outcome.failure_reason)
    except Exception as exc:
        logger.error(
            "emails.process_inbound_email: process_email failed | job_id=%s user_id=%s error=%s",
            job_id, user_id, exc, exc_info=True,
        )
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature=_make_signature(exc))
        _log_attempt(job, 'failed', ScanJob.REASON_INTERNAL_ERROR)
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 5, 'countdown': 60},
    acks_late=True,
)
def process_uploaded_file(self, job_id: int, user_id: int, file_b64: str, media_type: str, context: str = '', filename: str = ''):
    """
    Process a dashboard file upload through the LLM pipeline.

    Routes through process_email (empty body, single attachment) so the
    unified pipeline and filename-context feature are used consistently.

    file_b64:  base64-encoded file contents (stored in task_args by the view).
    filename:  original filename — passed as context hint to the LLM.
    context:   optional user text, sent alongside the file.

    Retry: up to 5x, 60 s initial. acks_late: acked only after return.
    """
    from llm.pipeline import process_email, ProcessingOutcome

    if settings.DEBUG:
        logger.debug(
            "emails.process_uploaded_file: start | job_id=%s user_id=%s media_type=%s filename=%r",
            job_id, user_id, media_type, filename,
        )

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_uploaded_file: job not found | job_id=%s user_id=%s", job_id, user_id)
        return

    user = _get_user_or_fail(user_id, job)
    if user is None:
        return

    attachments = [[file_b64, media_type, filename]]
    body = context or ''

    _set_processing(job)
    try:
        outcome = process_email(user, body, attachments)
        if settings.DEBUG:
            logger.debug(
                "emails.process_uploaded_file: outcome | job_id=%s status=%s created=%s",
                job_id, outcome.status, len(outcome.created),
            )
        if not outcome.notes and not outcome.created and outcome.status == 'done':
            outcome.notes = 'No events found in this file.'
        _set_terminal(job, outcome)
        _set_notes(job, outcome.notes)
        _log_attempt(job, outcome.status, outcome.failure_reason)
    except Exception as exc:
        logger.error(
            "emails.process_uploaded_file: process_email failed | job_id=%s user_id=%s error=%s",
            job_id, user_id, exc, exc_info=True,
        )
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature=_make_signature(exc))
        _log_attempt(job, 'failed', ScanJob.REASON_INTERNAL_ERROR)
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 5, 'countdown': 60},
    acks_late=True,
)
def process_text_as_upload(self, job_id: int, user_id: int, text: str):
    """
    User-initiated re-extraction from event_prompt_edit or bulk reprocess.

    The view deleted the original events and serialised them + the user's
    instruction into 'text'. This creates a fresh upload job — it is not a
    needs_review fix (reprocess_events handles that path).

    Retry: up to 5x, 60 s initial. acks_late: acked only after return.
    """
    from llm.pipeline import process_text, ProcessingOutcome

    if settings.DEBUG:
        logger.debug(
            "emails.process_text_as_upload: start | job_id=%s user_id=%s text_len=%s",
            job_id, user_id, len(text),
        )

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
        outcome = process_text(user, text)
        if settings.DEBUG:
            logger.debug(
                "emails.process_text_as_upload: outcome | job_id=%s status=%s created=%s",
                job_id, outcome.status, len(outcome.created),
            )
        if not outcome.notes and not outcome.created and outcome.status == 'done':
            outcome.notes = 'No events found.'
        _set_terminal(job, outcome)
        _set_notes(job, outcome.notes)
        _log_attempt(job, outcome.status, outcome.failure_reason)
    except Exception as exc:
        logger.error(
            "emails.process_text_as_upload: process_text failed | job_id=%s user_id=%s error=%s",
            job_id, user_id, exc, exc_info=True,
        )
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature=_make_signature(exc))
        _log_attempt(job, 'failed', ScanJob.REASON_INTERNAL_ERROR)
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 5, 'countdown': 60},
    acks_late=True,
)
def reprocess_events(self, user_id: int, event_ids: list, prompt: str, job_pk: int = None):
    """
    Reprocess a needs_review job after the user supplies a correction prompt.

    Contract:
    - NEVER creates a new ScanJob — the original job is mutated.
    - Reads and serialises pending event data BEFORE deleting them.
    - Deletes pending events only AFTER a successful LLM extraction.
    - Re-extracts using the user's prompt, preserving source_email_id.
    - _set_terminal writes all terminal state from the ProcessingOutcome.

    job_pk must be the pk of the original ScanJob being reviewed.
    """
    from dashboard.models import Event
    from llm.pipeline import process_text, ProcessingOutcome

    if job_pk is None:
        logger.error("emails.reprocess_events: job_pk missing | user_id=%s", user_id)
        return

    if settings.DEBUG:
        logger.debug(
            "emails.reprocess_events: start | job_pk=%s user_id=%s event_ids=%s",
            job_pk, user_id, event_ids,
        )

    try:
        job = ScanJob.objects.get(pk=job_pk, user_id=user_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.reprocess_events: job not found | job_pk=%s user_id=%s", job_pk, user_id)
        return

    user = _get_user_or_fail(user_id, job)
    if user is None:
        return

    # Preserve event data and source_email_id BEFORE any deletion.
    # Events are only deleted after a successful LLM call — if the LLM fails,
    # the pending events remain and the job stays recoverable.
    events_qs = Event.objects.filter(pk__in=event_ids, user=user, status='pending').select_related('category')
    events_list = list(events_qs)
    source_email_id = next((e.source_email_id for e in events_list if e.source_email_id), '')

    if not prompt.strip():
        # User cleared without a prompt — close the job.
        events_qs.delete()
        _set_terminal(job, ProcessingOutcome(status='done', notes='User cleared pending events.'))
        _log_attempt(job, 'done')
        return

    full_text = (
        "\n\n---\n\n".join(e.serialize_as_text() for e in events_list)
        + f"\n\nUser instruction: {prompt}"
    )

    if settings.DEBUG:
        logger.debug(
            "emails.reprocess_events: running pipeline | job_pk=%s text_len=%s source_email_id=%r",
            job_pk, len(full_text), source_email_id,
        )

    _set_processing(job)
    try:
        outcome = process_text(
            user, full_text,
            source_email_id=source_email_id,
        )
        # LLM succeeded — safe to delete old pending events now.
        events_qs.delete()
        if settings.DEBUG:
            logger.debug(
                "emails.reprocess_events: outcome | job_pk=%s status=%s created=%s",
                job_pk, outcome.status, len(outcome.created),
            )
        _set_terminal(job, outcome)
        _set_notes(job, outcome.notes)
        _log_attempt(job, outcome.status, outcome.failure_reason)
    except Exception as exc:
        logger.error(
            "emails.reprocess_events: process_text failed | job_pk=%s user_id=%s error=%s",
            job_pk, user_id, exc, exc_info=True,
        )
        _set_failed(job, reason=ScanJob.REASON_INTERNAL_ERROR, signature=_make_signature(exc))
        _log_attempt(job, 'failed', ScanJob.REASON_INTERNAL_ERROR)
        raise


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------

@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
def reset_monthly_scans():
    """
    Snapshot current-month token usage into MonthlyUsage, reset all monthly
    counters for users whose scan_reset_date is in a prior month, then
    re-enqueue any failed scan_limit jobs so they retry with the fresh quota.

    Scheduled: 1st of each month via Celery Beat.
    Retry: up to 3x, 5-minute delay.
    """
    from accounts.models import MonthlyUsage, User

    today = timezone.now().date()
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

    if settings.DEBUG:
        logger.debug("emails.reset_monthly_scans: reset %s user(s)", updated)

    _retry_failed_jobs(reason=ScanJob.REASON_SCAN_LIMIT)


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 60})
def retry_jobs_after_plan_upgrade(user_id: int):
    """
    Re-enqueue all failed jobs for a user blocked by plan restrictions.
    Called from the billing webhook after a successful subscription activation.

    Retry: up to 3x, 60 s delay.
    """
    jobs = list(ScanJob.objects.filter(
        user_id=user_id,
        status=ScanJob.STATUS_FAILED,
        failure_reason__in=[ScanJob.REASON_SCAN_LIMIT, ScanJob.REASON_PRO_REQUIRED],
    ))
    if settings.DEBUG:
        logger.debug(
            "emails.retry_jobs_after_plan_upgrade: re-enqueuing %s job(s) | user_id=%s",
            len(jobs), user_id,
        )
    _reenqueue_jobs(jobs)


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
def recover_stale_jobs():
    """
    Reset jobs stuck at 'processing' for over 10 minutes back to 'queued'
    and re-dispatch them via _dispatch_job.

    A job gets stuck when a worker crashes mid-task before setting terminal
    status. Without this task they stay at processing forever.

    Scheduled: every 10 minutes via Celery Beat.
    Retry: up to 3x, 5-minute delay.
    """
    cutoff = timezone.now() - timezone.timedelta(minutes=10)
    stale_jobs = list(
        ScanJob.objects.filter(status=ScanJob.STATUS_PROCESSING, updated_at__lt=cutoff)
    )
    if not stale_jobs:
        return
    if settings.DEBUG:
        logger.debug("emails.recover_stale_jobs: recovering %s stale job(s)", len(stale_jobs))
    _reenqueue_jobs(stale_jobs)


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 300})
def cleanup_events():
    """
    Daily cleanup:
      1. Delete expired pending events (pending_expires_at <= today).
      2. Delete past active events for users with auto_delete_past_events.
      3. Delete done ScanJobs older than 1 day.
         Failed jobs are never auto-deleted — they stay until the user acts.

    Retry: up to 3x, 5-minute delay.
    """
    from dashboard.models import Event
    from dashboard.gcal import delete_from_gcal
    from accounts.models import User

    today = timezone.now().date()

    expired_pending = Event.objects.filter(status='pending', pending_expires_at__lte=today)
    count = expired_pending.count()
    expired_pending.delete()
    if count:
        logger.info("emails.cleanup_events: deleted %s expired pending event(s)", count)

    for user in User.objects.filter(auto_delete_past_events=True):
        cutoff = timezone.now() - timezone.timedelta(days=user.past_event_retention_days)
        for event in Event.objects.filter(user=user, status='active', end__lt=cutoff):
            event._skip_gcal_delete = True
            if user.delete_from_gcal_on_cleanup and event.google_event_id:
                delete_from_gcal(user, event.google_event_id)
            event.delete()

    job_cutoff = timezone.now() - timezone.timedelta(days=1)
    ScanJob.objects.filter(status=ScanJob.STATUS_DONE, updated_at__lt=job_cutoff).delete()


@shared_task(autoretry_for=(Exception,), retry_kwargs={'max_retries': 2, 'countdown': 300})
def cleanup_job_attempt_logs():
    """
    Delete job attempt logs older than 30 days.
    Keeps metrics queryable for the last month without unbounded table growth.

    Retry: up to 2x, 5-minute delay.
    """
    cutoff = timezone.now() - timezone.timedelta(days=30)
    JobAttemptLog.objects.filter(created_at__lt=cutoff).delete()


# ---------------------------------------------------------------------------
# Internal helpers (called by scheduled tasks above)
# ---------------------------------------------------------------------------

def _retry_failed_jobs(reason: str) -> int:
    """Re-enqueue all failed jobs with a given failure_reason. Returns count."""
    jobs = list(ScanJob.objects.filter(status=ScanJob.STATUS_FAILED, failure_reason=reason))
    return _reenqueue_jobs(jobs)


def _reenqueue_jobs(jobs: list) -> int:
    """
    Reset a list of ScanJob instances to 'queued' and dispatch each one via
    _dispatch_job, which reads stored task_args and calls the original task.

    Returns the number of jobs successfully re-enqueued.
    """
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
            _dispatch_job(job)
            count += 1
        except Exception as exc:
            logger.error("emails._reenqueue_jobs: failed | job_id=%s error=%s", job.pk, exc)
    return count
