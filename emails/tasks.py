# emails/tasks.py
import logging
from fnmatch import fnmatch

from django.conf import settings
from django.utils import timezone
from procrastinate.contrib.django import app
from procrastinate import RetryStrategy

from emails.models import ScanJob

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry strategies
# ---------------------------------------------------------------------------

# Transient failures (network, API): linear backoff, up to 5 attempts.
# Procrastinate stores the retry schedule in Postgres — no broker message
# to lose. Worker crash releases the row lock; another worker picks it up.
_transient_retry = RetryStrategy(max_attempts=5, linear_wait=60)


# ---------------------------------------------------------------------------
# Sender rule check
# ---------------------------------------------------------------------------

def _check_sender_rules(user, sender: str) -> tuple[bool, str]:
    """
    Evaluate sender-type Rule rows for allow/block actions.

    Returns (is_blocked, note) where note is a human-readable explanation
    to store on the job if blocked.

    Pattern matching supports exact address, @domain suffix, and fnmatch globs.
    """
    from dashboard.models import Rule

    sender_rules = Rule.objects.filter(
        user=user,
        rule_type=Rule.TYPE_SENDER,
        action__in=[Rule.ACTION_ALLOW, Rule.ACTION_BLOCK],
    )

    if not sender_rules.exists():
        return False, ''

    sender_lower = sender.lower()

    def matches(pattern: str) -> bool:
        p = pattern.lower()
        if p.startswith('@'):
            return sender_lower.endswith(p)
        return sender_lower == p or fnmatch(sender_lower, p)

    allow_patterns = [r.pattern for r in sender_rules if r.action == Rule.ACTION_ALLOW]
    block_patterns = [r.pattern for r in sender_rules if r.action == Rule.ACTION_BLOCK]

    if any(matches(p) for p in block_patterns):
        return True, f'Discarded — sender blocked by rule: {sender}'

    if allow_patterns and not any(matches(p) for p in allow_patterns):
        return True, f'Discarded — sender not in allow list: {sender}'

    return False, ''


def _load_user(user_id: int, job_id: int):
    """Return User or None. Marks job failed and returns None if not found."""
    from accounts.models import User
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error(
            "emails: user not found | user_id=%s job_id=%s", user_id, job_id,
        )
        ScanJob.objects.filter(pk=job_id).update(
            status=ScanJob.STATUS_FAILED,
            failure_reason=ScanJob.REASON_INTERNAL_ERROR,
            notes='User account not found.',
        )
        return None


def _apply_outcome(job_id: int, outcome) -> None:
    """Write terminal job state from a ProcessingOutcome."""
    ScanJob.objects.filter(pk=job_id).update(
        status=outcome.status,
        failure_reason=outcome.failure_reason[:30] if outcome.failure_reason else '',
        notes=outcome.notes[:255] if outcome.notes else '',
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.task(retry=_transient_retry)
def track_llm_usage(user_id: int, input_tokens: int, output_tokens: int) -> None:
    """
    Atomically increment current-month LLM token counters for a user.
    Fired async by pipeline after every Anthropic API response.
    """
    from django.db.models import F
    from accounts.models import User
    try:
        User.objects.filter(pk=user_id).update(
            monthly_input_tokens=F('monthly_input_tokens') + input_tokens,
            monthly_output_tokens=F('monthly_output_tokens') + output_tokens,
        )
    except Exception as exc:
        logger.error("emails.track_llm_usage: update failed | user_id=%s error=%s", user_id, exc)
        raise


@app.task(retry=_transient_retry)
def process_inbound_email(job_id: int, user_id: int, email_id: str, sender: str, message_id: str) -> None:
    """
    Process a single inbound email through the LLM pipeline.

    Flow:
      1. Load ScanJob and User
      2. Sender rule check — closes job as done if blocked
      3. Duplicate guard on message_id
      4. Mark job processing
      5. Fetch full email from Resend (retried by Procrastinate on failure)
      6. Run process_email → ProcessingOutcome
      7. Write terminal state

    Retry: up to 5x, linear 60s backoff, stored in Postgres.
    Worker crash: Procrastinate releases the lock; another worker retries.
    """
    from dashboard.models import Event
    from llm.pipeline import process_email
    from emails.webhook import fetch_full_email, extract_email_text, extract_attachments

    if settings.DEBUG:
        logger.debug(
            "emails.process_inbound_email: start | job_id=%s user_id=%s email_id=%s sender=%s",
            job_id, user_id, email_id, sender,
        )

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_inbound_email: job not found | job_id=%s", job_id)
        return

    user = _load_user(user_id, job_id)
    if user is None:
        return

    is_blocked, block_note = _check_sender_rules(user, sender)
    if is_blocked:
        ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_DONE, notes=block_note[:255])
        return

    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        ScanJob.objects.filter(pk=job_id).update(
            status=ScanJob.STATUS_DONE,
            notes='Email already processed — skipped.',
        )
        return

    ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_PROCESSING)

    full_email = fetch_full_email(email_id)
    if not full_email:
        raise RuntimeError(f"fetch_full_email returned empty | job_id={job_id} email_id={email_id}")

    body = extract_email_text(full_email)
    attachments = extract_attachments(full_email)

    outcome = process_email(
        user, body, attachments,
        sender=sender,
        source_email_id=message_id,
        scan_job=job,
    )

    if not outcome.notes and not outcome.created and outcome.status == 'done':
        outcome.notes = 'No events found in this email.'

    _apply_outcome(job_id, outcome)

    if settings.DEBUG:
        logger.debug(
            "emails.process_inbound_email: done | job_id=%s status=%s created=%s",
            job_id, outcome.status, len(outcome.created),
        )

    # Re-raise on internal_error so Procrastinate retries.
    # llm_error and scan_limit are terminal — user sees them and acts.
    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_id={job_id}")


@app.task(retry=_transient_retry)
def process_uploaded_file(job_id: int, user_id: int, file_b64: str, media_type: str, context: str = '', filename: str = '') -> None:
    """
    Process a dashboard file upload through the LLM pipeline.
    """
    from llm.pipeline import process_email

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_uploaded_file: job not found | job_id=%s", job_id)
        return

    user = _load_user(user_id, job_id)
    if user is None:
        return

    ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_PROCESSING)

    outcome = process_email(user, context or '', [[file_b64, media_type, filename]], scan_job=job)

    if not outcome.notes and not outcome.created and outcome.status == 'done':
        outcome.notes = 'No events found in this file.'

    _apply_outcome(job_id, outcome)

    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_id={job_id}")


@app.task(retry=_transient_retry)
def process_text_as_upload(job_id: int, user_id: int, text: str) -> None:
    """
    User-initiated re-extraction from event_prompt_edit or bulk reprocess.

    The view deleted the original events and serialised them + the user's
    instruction into 'text'. Creates a fresh upload job — not a needs_review fix.
    """
    from llm.pipeline import process_text

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_text_as_upload: job not found | job_id=%s", job_id)
        return

    user = _load_user(user_id, job_id)
    if user is None:
        return

    ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_PROCESSING)

    outcome = process_text(user, text, scan_job=job)

    if not outcome.notes and not outcome.created and outcome.status == 'done':
        outcome.notes = 'No events found.'

    _apply_outcome(job_id, outcome)

    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_id={job_id}")


@app.task(retry=_transient_retry)
def reprocess_events(user_id: int, event_ids: list, prompt: str, job_pk: int) -> None:
    """
    Reprocess a needs_review job after the user supplies a correction prompt.

    Contract:
    - NEVER creates a new ScanJob — the original job is mutated.
    - Reads and serialises pending event data BEFORE deleting them.
    - Deletes pending events only AFTER a successful LLM extraction.
    - Re-extracts using the user's prompt, preserving source_email_id.
    """
    from dashboard.models import Event
    from llm.pipeline import process_text

    try:
        job = ScanJob.objects.get(pk=job_pk, user_id=user_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.reprocess_events: job not found | job_pk=%s", job_pk)
        return

    user = _load_user(user_id, job_pk)
    if user is None:
        return

    events_qs = Event.objects.filter(pk__in=event_ids, user=user, status='pending').select_related('category')
    events_list = list(events_qs)
    source_email_id = next((e.source_email_id for e in events_list if e.source_email_id), '')

    if not prompt.strip():
        events_qs.delete()
        ScanJob.objects.filter(pk=job_pk).update(
            status=ScanJob.STATUS_DONE,
            notes='User cleared pending events.',
        )
        return

    full_text = (
        "\n\n---\n\n".join(e.serialize_as_text() for e in events_list)
        + f"\n\nUser instruction: {prompt}"
    )

    ScanJob.objects.filter(pk=job_pk).update(status=ScanJob.STATUS_PROCESSING)

    outcome = process_text(user, full_text, source_email_id=source_email_id, scan_job=job)

    # LLM succeeded — safe to delete old pending events now.
    events_qs.delete()

    _apply_outcome(job_pk, outcome)

    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_pk={job_pk}")


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------

@app.periodic(cron="0 0 1 * *")
@app.task
def reset_monthly_scans(timestamp: int) -> None:
    """
    Snapshot current-month token usage into MonthlyUsage, reset all monthly
    counters, then re-enqueue any failed scan_limit jobs.

    Scheduled: 1st of each month at midnight UTC.
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

    _retry_failed_jobs(ScanJob.REASON_SCAN_LIMIT)


@app.task(retry=RetryStrategy(max_attempts=3, wait=60))
def retry_jobs_after_plan_upgrade(user_id: int) -> None:
    """
    Re-enqueue all failed jobs for a user blocked by plan restrictions.
    Called from the billing webhook after a successful subscription activation.
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
    _retry_jobs(jobs)


@app.periodic(cron="*/10 * * * *")
@app.task
def recover_stale_jobs(timestamp: int) -> None:
    """
    Reset ScanJobs stuck in 'processing' for longer than 10 minutes back to
    'queued' and re-enqueue them.

    A job gets stuck when a worker crashes mid-task before it can set a
    terminal status. Without this, those jobs stay at 'processing' forever.
    Procrastinate's row-level locking means a crashed worker's lock is
    released automatically, but ScanJob.status is our own field — it still
    needs to be reconciled.

    Scheduled: every 10 minutes.
    """
    cutoff = timezone.now() - timezone.timedelta(minutes=10)
    stale = list(
        ScanJob.objects.filter(status=ScanJob.STATUS_PROCESSING, updated_at__lt=cutoff)
    )
    if not stale:
        return

    _reenqueue_jobs(stale)
    logger.info("emails.recover_stale_jobs: recovered %s stale job(s)", len(stale))


@app.periodic(cron="0 2 * * *")
@app.task
def cleanup_events(timestamp: int) -> None:
    """
    Daily cleanup at 2 AM UTC:
      1. Delete expired pending events.
      2. Delete past active events for users with auto_delete_past_events.
      3. Delete done ScanJobs older than 1 day.
         Failed and needs_review jobs are never auto-deleted.
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _retry_failed_jobs(reason: str) -> None:
    """Re-enqueue all globally failed jobs with a given failure_reason."""
    jobs = list(ScanJob.objects.filter(status=ScanJob.STATUS_FAILED, failure_reason=reason))
    _retry_jobs(jobs)


def _retry_jobs(jobs: list) -> None:
    """
    Reset ScanJob instances to queued and defer the appropriate task.

    Procrastinate stores the task args in Postgres and guarantees delivery.
    No task_args blob needed — typed fields on ScanJob hold what's required.
    Worker crashes release the Postgres lock automatically.
    """
    for job in jobs:
        try:
            ScanJob.objects.filter(pk=job.pk).update(
                status=ScanJob.STATUS_QUEUED,
                failure_reason='',
                notes='Queued for retry.',
                updated_at=timezone.now(),
            )

            if job.source == ScanJob.SOURCE_EMAIL:
                process_inbound_email.defer(
                    job_id=job.pk,
                    user_id=job.user_id,
                    email_id=job.email_id,
                    sender=job.from_address,
                    message_id=job.message_id,
                )
            elif job.source == ScanJob.SOURCE_UPLOAD:
                if job.upload_text:
                    process_text_as_upload.defer(
                        job_id=job.pk,
                        user_id=job.user_id,
                        text=job.upload_text,
                    )
                else:
                    process_uploaded_file.defer(
                        job_id=job.pk,
                        user_id=job.user_id,
                        file_b64=job.file_b64,
                        media_type=job.media_type,
                        context=job.upload_context,
                        filename=job.filename,
                    )
            else:
                logger.error(
                    "emails._retry_jobs: unknown source | job_id=%s source=%s",
                    job.pk, job.source,
                )

        except Exception as exc:
            logger.error("emails._retry_jobs: failed | job_id=%s error=%s", job.pk, exc)