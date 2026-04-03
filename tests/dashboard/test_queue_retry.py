import pytest
from unittest.mock import patch
from django.urls import reverse
from emails.models import ScanJob


@pytest.mark.django_db
class TestQueueJobRetry:
    @patch('emails.tasks._retry_jobs')
    def test_retries_failed(self, mock_retry, auth_client, user):
        job = ScanJob.objects.create(
            user=user, source='email', status=ScanJob.STATUS_FAILED,
            failure_reason=ScanJob.REASON_LLM_ERROR,
        )
        resp = auth_client.post(reverse('dashboard:queue_job_retry', args=[job.pk]))
        assert resp.json()['ok']
        mock_retry.assert_called_once()

    @patch('emails.tasks._retry_jobs')
    def test_retries_needs_review(self, mock_retry, auth_client, user):
        job = ScanJob.objects.create(
            user=user, source='upload', status=ScanJob.STATUS_NEEDS_REVIEW,
        )
        resp = auth_client.post(reverse('dashboard:queue_job_retry', args=[job.pk]))
        assert resp.json()['ok']
        mock_retry.assert_called_once()

    def test_retry_rejects_done(self, auth_client, user):
        job = ScanJob.objects.create(
            user=user, source='upload', status=ScanJob.STATUS_DONE,
        )
        resp = auth_client.post(reverse('dashboard:queue_job_retry', args=[job.pk]))
        assert resp.status_code == 500 or not resp.json().get('ok', True)
