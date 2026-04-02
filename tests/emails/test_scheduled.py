import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone as dt_tz, timedelta, date
from django.utils import timezone
from emails.models import ScanJob
from emails.tasks.scheduled import cleanup_events, recover_stale_jobs, reset_monthly_scans
from dashboard.models import Event


@pytest.mark.django_db
class TestCleanupEvents:
    def test_deletes_expired_pending(self, user):
        Event.objects.create(
            user=user, title='Old',
            start=datetime(2026, 1, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 1, 1, 10, tzinfo=dt_tz.utc),
            status='pending', pending_expires_at=date(2020, 1, 1),
        )
        cleanup_events(0)
        assert Event.objects.filter(title='Old').count() == 0

    @patch('dashboard.gcal.crud.delete_from_gcal')
    def test_deletes_past_active(self, mock_gcal, user):
        user.auto_delete_past_events = True
        user.past_event_retention_days = 1
        user.save()
        Event.objects.create(
            user=user, title='Past',
            start=datetime(2024, 1, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2024, 1, 1, 10, tzinfo=dt_tz.utc),
        )
        cleanup_events(0)
        assert Event.objects.filter(title='Past').count() == 0

    def test_deletes_done_jobs(self, user):
        job = ScanJob.objects.create(user=user, source='email', status='done')
        ScanJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(days=2),
        )
        cleanup_events(0)
        assert not ScanJob.objects.filter(pk=job.pk).exists()

    def test_deletes_old_needs_review_jobs(self, user):
        job = ScanJob.objects.create(user=user, source='upload', status='needs_review')
        ScanJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(days=31),
        )
        cleanup_events(0)
        assert not ScanJob.objects.filter(pk=job.pk).exists()


@pytest.mark.django_db
class TestRecoverStaleJobs:
    @patch('emails.tasks.retry._retry_jobs')
    def test_recovers(self, mock_retry, user):
        job = ScanJob.objects.create(user=user, source='email', status='processing')
        ScanJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(minutes=15),
        )
        recover_stale_jobs(0)
        mock_retry.assert_called_once()


@pytest.mark.django_db
class TestResetMonthlyScans:
    @patch('emails.tasks.retry._retry_failed_jobs')
    def test_resets_and_archives(self, mock_retry, user):
        from accounts.models import MonthlyUsage, User
        last_month = timezone.now().date().replace(day=1) - timedelta(days=1)
        User.objects.filter(pk=user.pk).update(
            monthly_scans=15, monthly_input_tokens=5000,
            monthly_output_tokens=2000, scan_reset_date=last_month,
        )
        reset_monthly_scans(0)
        user.refresh_from_db()
        assert user.monthly_scans == 0
        assert user.monthly_input_tokens == 0
        assert user.monthly_output_tokens == 0
        usage = MonthlyUsage.objects.get(user=user)
        assert usage.input_tokens == 5000
        assert usage.output_tokens == 2000
        mock_retry.assert_called_once_with(ScanJob.REASON_SCAN_LIMIT)
