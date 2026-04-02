import logging

from django.conf import settings
from django.utils import timezone
from procrastinate.contrib.django import app
from procrastinate import RetryStrategy

from emails.models import ScanJob

logger = logging.getLogger(__name__)


@app.task(retry=RetryStrategy(max_attempts=3, wait=60))
def retry_jobs_after_plan_upgrade(user_id: int) -> None:
    jobs = list(ScanJob.objects.filter(
        user_id=user_id, status=ScanJob.STATUS_FAILED,
        failure_reason__in=[ScanJob.REASON_SCAN_LIMIT, ScanJob.REASON_PRO_REQUIRED],
    ))
    _retry_jobs(jobs)


def _retry_failed_jobs(reason: str) -> None:
    jobs = list(ScanJob.objects.filter(status=ScanJob.STATUS_FAILED, failure_reason=reason))
    _retry_jobs(jobs)


def _retry_jobs(jobs: list) -> None:
    for job in jobs:
        try:
            ScanJob.objects.filter(pk=job.pk).update(
                status=ScanJob.STATUS_QUEUED, failure_reason='',
                notes='Queued for retry.', updated_at=timezone.now(),
            )
            if job.source == ScanJob.SOURCE_EMAIL:
                from .processing import process_inbound_email
                process_inbound_email.defer(
                    job_id=job.pk, user_id=job.user_id, email_id=job.email_id,
                    sender=job.from_address, message_id=job.message_id,
                )
            elif job.source == ScanJob.SOURCE_UPLOAD:
                if job.upload_text:
                    from .processing import process_text_as_upload
                    process_text_as_upload.defer(job_id=job.pk, user_id=job.user_id, text=job.upload_text)
                else:
                    from .processing import process_uploaded_file
                    process_uploaded_file.defer(
                        job_id=job.pk, user_id=job.user_id, file_b64=job.file_b64,
                        media_type=job.media_type, context=job.upload_context, filename=job.filename,
                    )
            else:
                logger.error("emails._retry_jobs: unknown source | job_id=%s source=%s", job.pk, job.source)
        except Exception as exc:
            logger.error("emails._retry_jobs: failed | job_id=%s error=%s", job.pk, exc)
