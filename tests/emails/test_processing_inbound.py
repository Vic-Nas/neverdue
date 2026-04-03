import pytest
from unittest.mock import patch, MagicMock
from emails.models import ScanJob
from emails.tasks.processing import process_inbound_email


def _make_outcome(status='done', failure_reason='', notes='', created=None):
    o = MagicMock()
    o.status, o.failure_reason, o.notes = status, failure_reason, notes
    o.created = created or []
    return o


_P_FETCH = 'emails.webhook.fetch_full_email'
_P_TEXT = 'emails.webhook.extract_email_text'
_P_ATT = 'emails.webhook.extract_attachments'
_P_PIPE = 'llm.pipeline.process_email'


@pytest.mark.django_db
class TestProcessInboundEmail:
    @patch(_P_PIPE)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_success_done(self, _f, _t, _a, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('done', created=[MagicMock()])
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE

    @patch(_P_PIPE)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_needs_review(self, _f, _t, _a, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('needs_review', notes='Conflicts')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW

    @patch(_P_PIPE)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_no_events_note(self, _f, _t, _a, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('done', created=[], notes='')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert 'No events found' in job.notes

    @patch(_P_PIPE)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_llm_error_fails(self, _f, _t, _a, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('failed', failure_reason='llm_error')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_FAILED
        assert job.failure_reason == 'llm_error'
