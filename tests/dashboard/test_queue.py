import pytest
from unittest.mock import patch, MagicMock
from django.urls import reverse
from dashboard.models import Event
from dashboard.views.queue import queue_status
from emails.models import ScanJob
from datetime import datetime, timezone as dt_tz


@pytest.fixture
def job(user):
    return ScanJob.objects.create(
        user=user, source='upload', status=ScanJob.STATUS_DONE,
    )


@pytest.mark.django_db
class TestQueuePage:
    def test_renders(self, auth_client):
        resp = auth_client.get(reverse('dashboard:queue'))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestQueueStatus:
    def test_returns_jobs(self, auth_client, job):
        resp = auth_client.get(reverse('dashboard:queue_status'))
        data = resp.json()
        assert 'jobs' in data
        assert len(data['jobs']) >= 1


@pytest.mark.django_db
class TestQueueJobDetail:
    def test_renders(self, auth_client, job):
        resp = auth_client.get(reverse('dashboard:queue_job_detail', args=[job.pk]))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestQueueJobReprocess:
    @patch('emails.tasks.reprocess.reprocess_events.defer')
    def test_defers(self, mock_defer, auth_client, user):
        job = ScanJob.objects.create(user=user, source='upload', status=ScanJob.STATUS_NEEDS_REVIEW)
        import json
        resp = auth_client.post(
            reverse('dashboard:queue_job_reprocess', args=[job.pk]),
            json.dumps({'prompt': 'Fix dates', 'event_ids': []}),
            content_type='application/json',
        )
        assert resp.json()['ok']


@pytest.mark.django_db
class TestQueueJobRetry:
    @patch('emails.tasks.retry._retry_jobs')
    def test_retries(self, mock_retry, auth_client, user):
        job = ScanJob.objects.create(
            user=user, source='email', status=ScanJob.STATUS_FAILED,
            failure_reason=ScanJob.REASON_LLM_ERROR,
        )
        resp = auth_client.post(reverse('dashboard:queue_job_retry', args=[job.pk]))
        assert resp.json()['ok']
