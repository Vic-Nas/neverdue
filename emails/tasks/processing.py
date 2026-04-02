# emails/tasks/processing.py
import logging

from django.conf import settings
from django.utils import timezone
from procrastinate.contrib.django import app

from emails.models import ScanJob
from .helpers import _transient_retry, _check_sender_rules, _load_user, _apply_outcome

logger = logging.getLogger(__name__)


@app.task(retry=_transient_retry)
def process_inbound_email(job_id: int, user_id: int, email_id: str, sender: str, message_id: str) -> None:
    from dashboard.models import Event
    from llm.pipeline import process_email
    from emails.webhook import fetch_full_email, extract_email_text, extract_attachments

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
        ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_DONE, notes=block_note[:255], updated_at=timezone.now())
        return

    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_DONE, notes='Email already processed — skipped.', updated_at=timezone.now())
        return

    ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_PROCESSING, updated_at=timezone.now())

    logger.debug("process_inbound_email: fetching email | job_id=%s email_id=%s", job_id, email_id)
    full_email = fetch_full_email(email_id)
    if not full_email:
        raise RuntimeError(f"fetch_full_email returned empty | job_id={job_id} email_id={email_id}")

    body = extract_email_text(full_email)
    attachments = extract_attachments(full_email)
    logger.debug("process_inbound_email: extracted body=%d chars, %d attachments | job_id=%s", len(body or ''), len(attachments), job_id)

    outcome = process_email(user, body, attachments, sender=sender, source_email_id=message_id, scan_job=job)
    logger.debug("process_inbound_email: outcome status=%s created=%d | job_id=%s", outcome.status, len(outcome.created), job_id)

    if not outcome.notes and not outcome.created and outcome.status == 'done':
        outcome.notes = 'No events found in this email.'

    _apply_outcome(job_id, outcome)

    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_id={job_id}")


@app.task(retry=_transient_retry)
def process_uploaded_file(job_id: int, user_id: int, attachments: list, context: str = '') -> None:
    from llm.pipeline import process_email

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_uploaded_file: job not found | job_id=%s", job_id)
        return

    user = _load_user(user_id, job_id)
    if user is None:
        return

    ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_PROCESSING, updated_at=timezone.now())
    logger.debug("process_uploaded_file: starting | job_id=%s attachments=%d context_len=%d", job_id, len(attachments), len(context or ''))
    outcome = process_email(user, context or '', attachments, scan_job=job)
    logger.debug("process_uploaded_file: outcome status=%s created=%d | job_id=%s", outcome.status, len(outcome.created), job_id)

    if not outcome.created and outcome.status == 'done':
        outcome.status = 'needs_review'
        outcome.notes = 'No events found — the file may need a different format or more context.'

    _apply_outcome(job_id, outcome)
    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_id={job_id}")


@app.task(retry=_transient_retry)
def process_text_as_upload(job_id: int, user_id: int, text: str) -> None:
    from llm.pipeline import process_text

    try:
        job = ScanJob.objects.get(pk=job_id)
    except ScanJob.DoesNotExist:
        logger.error("emails.process_text_as_upload: job not found | job_id=%s", job_id)
        return

    user = _load_user(user_id, job_id)
    if user is None:
        return

    ScanJob.objects.filter(pk=job_id).update(status=ScanJob.STATUS_PROCESSING, updated_at=timezone.now())
    logger.debug("process_text_as_upload: starting | job_id=%s text_len=%d", job_id, len(text))
    outcome = process_text(user, text, scan_job=job)
    logger.debug("process_text_as_upload: outcome status=%s created=%d | job_id=%s", outcome.status, len(outcome.created), job_id)

    if not outcome.created and outcome.status == 'done':
        outcome.status = 'needs_review'
        outcome.notes = 'No events found — try rephrasing or adding more detail.'

    _apply_outcome(job_id, outcome)
    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_id={job_id}")
