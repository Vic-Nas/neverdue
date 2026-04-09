# emails/tasks/scheduled.py
import logging

from django.db.models import Count
from django.utils import timezone
from procrastinate.contrib.django import app

from emails.models import ScanJob

logger = logging.getLogger(__name__)


def _snapshot_jobs_to_daily_stats(qs):
    """
    Aggregate a ScanJob queryset by (date, status, failure_reason) and upsert
    into DailyJobStats. Call this immediately before bulk-deleting jobs so the
    staff dashboard retains historical counts after cleanup.

    Uses update_or_create so re-running cleanup on the same day is safe.
    """
    from emails.models import DailyJobStats

    rows = (
        qs
        .extra(select={'day': 'DATE(updated_at)'})
        .values('day', 'status', 'failure_reason')
        .annotate(n=Count('pk'))
    )
    for row in rows:
        DailyJobStats.objects.update_or_create(
            date=row['day'],
            status=row['status'],
            failure_reason=row['failure_reason'] or '',
            defaults={},  # nothing to update — we accumulate below
        )
        # Increment rather than overwrite so two cleanup runs don't double-count.
        DailyJobStats.objects.filter(
            date=row['day'],
            status=row['status'],
            failure_reason=row['failure_reason'] or '',
        ).update(count=models_F('count') + row['n'])


# Lazy import to avoid circular issues at module load time.
def _F():
    from django.db.models import F
    return F


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
    from django.db.models import F
    from dashboard.models import Event
    from dashboard.gcal import delete_from_gcal
    from accounts.models import User
    from emails.models import DailyJobStats

    today = timezone.now().date()

    # ── Expired pending events ────────────────────────────────────────────────
    expired_pending = Event.objects.filter(status='pending', pending_expires_at__lte=today)
    count = expired_pending.count()
    expired_pending.delete()
    if count:
        logger.info("emails.cleanup_events: deleted %s expired pending event(s)", count)

    # ── Auto-delete past events (per user preference) ─────────────────────────
    for user in User.objects.filter(auto_delete_past_events=True):
        cutoff = timezone.now() - timezone.timedelta(days=user.past_event_retention_days)
        for event in Event.objects.filter(user=user, status='active', end__lt=cutoff):
            event._skip_gcal_delete = True
            if user.delete_from_gcal_on_cleanup and event.google_event_id:
                delete_from_gcal(user, event.google_event_id)
            event.delete()

    # ── Done jobs: snapshot then delete (done jobs deleted after 1 day) ───────
    job_cutoff = timezone.now() - timezone.timedelta(days=1)
    done_qs = ScanJob.objects.filter(status=ScanJob.STATUS_DONE, updated_at__lt=job_cutoff)

    done_rows = list(
        done_qs
        .extra(select={'day': 'DATE(updated_at)'})
        .values('day', 'status', 'failure_reason')
        .annotate(n=Count('pk'))
    )
    for row in done_rows:
        obj, created = DailyJobStats.objects.get_or_create(
            date=row['day'],
            status=row['status'],
            failure_reason=row['failure_reason'] or '',
            defaults={'count': 0},
        )
        DailyJobStats.objects.filter(pk=obj.pk).update(count=F('count') + row['n'])

    deleted_done, _ = done_qs.delete()
    if deleted_done:
        logger.info("emails.cleanup_events: snapshotted and deleted %s done job(s)", deleted_done)

    # ── Needs-review jobs: snapshot then delete (after 30 days) ──────────────
    # The events they created still exist independently; the job row is bookkeeping.
    review_cutoff = timezone.now() - timezone.timedelta(days=30)
    review_qs = ScanJob.objects.filter(
        status=ScanJob.STATUS_NEEDS_REVIEW, updated_at__lt=review_cutoff,
    )

    review_rows = list(
        review_qs
        .extra(select={'day': 'DATE(updated_at)'})
        .values('day', 'status', 'failure_reason')
        .annotate(n=Count('pk'))
    )
    for row in review_rows:
        obj, created = DailyJobStats.objects.get_or_create(
            date=row['day'],
            status=row['status'],
            failure_reason=row['failure_reason'] or '',
            defaults={'count': 0},
        )
        DailyJobStats.objects.filter(pk=obj.pk).update(count=F('count') + row['n'])

    deleted_review, _ = review_qs.delete()
    if deleted_review:
        logger.info(
            "emails.cleanup_events: snapshotted and deleted %s needs_review job(s)",
            deleted_review,
        )


@app.periodic(cron="0 3 * * *")
@app.task
def cleanup_old_tickets(timestamp: int) -> None:
    """Delete support tickets older than 30 days."""
    from support.models import Ticket
    cutoff = timezone.now() - timezone.timedelta(days=30)
    deleted, _ = Ticket.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        logger.info("support.cleanup_old_tickets: deleted %s ticket(s)", deleted)


@app.periodic(cron="0 4 * * *")
@app.task
def cleanup_expired_referral_codes(timestamp: int) -> None:
    """
    Null out referral codes generated more than 30 days ago with no referrals.
    Codes are kept if the user has at least one referred user (referrals.exists()).
    """
    from django.utils import timezone
    from datetime import timedelta
    from billing.models import Subscription

    cutoff = timezone.now() - timedelta(days=30)
    candidates = Subscription.objects.filter(
        referral_code__isnull=False,
        referral_code_generated_at__lt=cutoff,
    ).select_related('user')

    expired = 0
    for sub in candidates:
        if not sub.user.referrals.exists():
            sub.referral_code = None
            sub.referral_code_generated_at = None
            sub.save(update_fields=['referral_code', 'referral_code_generated_at'])
            expired += 1

    if expired:
        logger.info('billing.cleanup_expired_referral_codes: expired %s code(s)', expired)
