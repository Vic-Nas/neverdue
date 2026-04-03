import pytest
from unittest.mock import patch
from datetime import timedelta
from django.utils import timezone
from emails.models import ScanJob
from emails.tasks.scheduled import recover_stale_jobs, reset_monthly_scans


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
