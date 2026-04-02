import pytest
from unittest.mock import patch, MagicMock
from emails.models import ScanJob
from emails.tasks.retry import _retry_jobs, retry_jobs_after_plan_upgrade


@pytest.mark.django_db
class TestRetryJobs:
    @patch('emails.tasks.processing.process_inbound_email')
    def test_retries_email_job(self, mock_proc, user):
        job = ScanJob.objects.create(
            user=user, source='email', status='failed',
            failure_reason='llm_error', email_id='eid', message_id='mid',
            from_address='a@b.com',
        )
        _retry_jobs([job])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_QUEUED
        assert job.failure_reason == ''
        mock_proc.defer.assert_called_once()

    @patch('emails.tasks.processing.process_text_as_upload')
    def test_retries_text_upload(self, mock_proc, user):
        job = ScanJob.objects.create(
            user=user, source='upload', status='failed',
            failure_reason='llm_error', upload_text='Exam on June 15',
        )
        _retry_jobs([job])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_QUEUED
        mock_proc.defer.assert_called_once()

    @patch('emails.tasks.processing.process_uploaded_file')
    def test_retries_file_upload(self, mock_proc, user):
        job = ScanJob.objects.create(
            user=user, source='upload', status='failed',
            failure_reason='llm_error',
            file_b64='base64data', media_type='image/png', filename='f.png',
        )
        _retry_jobs([job])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_QUEUED
        mock_proc.defer.assert_called_once()

    def test_unknown_source_logged(self, user):
        job = ScanJob.objects.create(user=user, status='failed', failure_reason='llm_error')
        # Force an unknown source value
        ScanJob.objects.filter(pk=job.pk).update(source='unknown')
        job.refresh_from_db()
        _retry_jobs([job])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_QUEUED  # still re-queued


@pytest.mark.django_db
class TestRetryAfterPlanUpgrade:
    @patch('emails.tasks.retry._retry_jobs')
    def test_finds_scan_limit_jobs(self, mock_retry, user):
        ScanJob.objects.create(
            user=user, source='email', status='failed',
            failure_reason='scan_limit',
        )
        ScanJob.objects.create(
            user=user, source='upload', status='failed',
            failure_reason='pro_required',
        )
        # Unrelated failure — should not be retried
        ScanJob.objects.create(
            user=user, source='email', status='failed',
            failure_reason='llm_error',
        )
        retry_jobs_after_plan_upgrade(user.pk)
        mock_retry.assert_called_once()
        retried = mock_retry.call_args[0][0]
        assert len(retried) == 2
