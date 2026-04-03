import pytest
from unittest.mock import patch
from emails.models import ScanJob
from emails.tasks.retry import retry_jobs_after_plan_upgrade


@pytest.mark.django_db
class TestRetryAfterPlanUpgrade:
    @patch('emails.tasks.retry._retry_jobs')
    def test_finds_scan_limit_jobs(self, mock_retry, user):
        ScanJob.objects.create(
            user=user, source='email', status='failed', failure_reason='scan_limit',
        )
        ScanJob.objects.create(
            user=user, source='upload', status='failed', failure_reason='pro_required',
        )
        ScanJob.objects.create(
            user=user, source='email', status='failed', failure_reason='llm_error',
        )
        retry_jobs_after_plan_upgrade(user.pk)
        mock_retry.assert_called_once()
        assert len(mock_retry.call_args[0][0]) == 2
