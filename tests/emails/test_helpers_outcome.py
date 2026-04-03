import pytest
from unittest.mock import MagicMock
from emails.tasks.helpers import _apply_outcome, track_llm_usage
from emails.models import ScanJob


@pytest.mark.django_db
class TestApplyOutcome:
    def test_done_purges_raw(self, user):
        job = ScanJob.objects.create(
            user=user, source='upload', file_b64='data', upload_text='text',
        )
        outcome = MagicMock(status='done', failure_reason='', notes='ok', created=[])
        _apply_outcome(job.pk, outcome)
        job.refresh_from_db()
        assert job.file_b64 == '' and job.upload_text == ''

    def test_failed_keeps_raw(self, user):
        job = ScanJob.objects.create(
            user=user, source='upload', file_b64='data', upload_text='text',
        )
        outcome = MagicMock(status='failed', failure_reason='llm_error', notes='err')
        _apply_outcome(job.pk, outcome)
        job.refresh_from_db()
        assert job.file_b64 == 'data' and job.upload_text == 'text'

    def test_needs_review_purges_raw(self, user):
        job = ScanJob.objects.create(
            user=user, source='upload', file_b64='data', upload_text='text',
        )
        outcome = MagicMock(status='needs_review', failure_reason='', notes='conflicts')
        _apply_outcome(job.pk, outcome)
        job.refresh_from_db()
        assert job.status == 'needs_review'
        assert job.file_b64 == '' and job.upload_text == ''


@pytest.mark.django_db
class TestTrackLlmUsage:
    def test_increments_tokens(self, user):
        from accounts.models import User
        User.objects.filter(pk=user.pk).update(monthly_input_tokens=100, monthly_output_tokens=50)
        track_llm_usage(user.pk, 200, 100)
        user.refresh_from_db()
        assert user.monthly_input_tokens == 300
        assert user.monthly_output_tokens == 150
