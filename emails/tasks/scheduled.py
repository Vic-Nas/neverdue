# emails/tasks/scheduled.py
import logging

from django.utils import timezone
from procrastinate.contrib.django import app
from procrastinate import RetryStrategy

from emails.models import ScanJob

logger = logging.getLogger(__name__)


@app.periodic(cron="0 0 1 * *")
@app.task
def reset_monthly_scans(timestamp: int) -> None:
    from accounts.models import MonthlyUsage, User
    from .retry import _retry_failed_jobs

    today = timezone.now().date()
    last_month = today.replace(day=1) - timezone.timedelta(days=1)
    users_to_reset = User.objects.filter(scan_reset_date__month__lt=today.month)

    for user in users_to_reset:
        MonthlyUsage.objects.update_or_create(
            user=user, year=last_month.year, month=last_month.month,
            defaults={
                'input_tokens': user.monthly_input_tokens,
                'output_tokens': user.monthly_output_tokens,
                'input_cost_per_million': '3.0000',
                'output_cost_per_million': '15.0000',
            },
        )

    users_to_reset.update(
        monthly_scans=0, monthly_input_tokens=0,
        monthly_output_tokens=0, scan_reset_date=today,
    )
    _retry_failed_jobs(ScanJob.REASON_SCAN_LIMIT)


@app.periodic(cron="*/10 * * * *")
@app.task
def recover_stale_jobs(timestamp: int) -> None:
    from .retry import _retry_jobs
    cutoff = timezone.now() - timezone.timedelta(minutes=10)
    stale = list(ScanJob.objects.filter(status=ScanJob.STATUS_PROCESSING, updated_at__lt=cutoff))
    if stale:
        _retry_jobs(stale)
        logger.info("emails.recover_stale_jobs: recovered %s stale job(s)", len(stale))


@app.periodic(cron="0 2 * * *")
@app.task
def cleanup_events(timestamp: int) -> None:
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

    # Delete needs_review jobs after 30 days — the events they created
    # still exist independently; the job row is only bookkeeping.
    review_cutoff = timezone.now() - timezone.timedelta(days=30)
    ScanJob.objects.filter(
        status=ScanJob.STATUS_NEEDS_REVIEW, updated_at__lt=review_cutoff,
    ).delete()
