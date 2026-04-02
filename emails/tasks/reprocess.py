# emails/tasks/reprocess.py
import logging

from django.utils import timezone
from procrastinate.contrib.django import app

from emails.models import ScanJob
from .helpers import _transient_retry, _load_user, _apply_outcome

logger = logging.getLogger(__name__)


@app.task(retry=_transient_retry)
def reprocess_events(user_id: int, event_ids: list, prompt: str, job_pk: int) -> None:
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
        ScanJob.objects.filter(pk=job_pk).update(status=ScanJob.STATUS_DONE, notes='User cleared pending events.', updated_at=timezone.now())
        return

    full_text = (
        "\n\n---\n\n".join(e.serialize_as_text() for e in events_list)
        + f"\n\nUser instruction: {prompt}"
    )

    ScanJob.objects.filter(pk=job_pk).update(status=ScanJob.STATUS_PROCESSING, updated_at=timezone.now())
    outcome = process_text(user, full_text, source_email_id=source_email_id, scan_job=job)
    events_qs.delete()
    _apply_outcome(job_pk, outcome)

    if outcome.failure_reason == ScanJob.REASON_INTERNAL_ERROR:
        raise RuntimeError(f"pipeline internal_error | job_pk={job_pk}")
