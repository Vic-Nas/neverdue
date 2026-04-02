import pytest
from unittest.mock import patch, MagicMock
from emails.models import ScanJob
from emails.tasks.processing import process_inbound_email, process_uploaded_file, process_text_as_upload


def _make_outcome(status='done', failure_reason='', notes='', created=None):
    o = MagicMock()
    o.status = status
    o.failure_reason = failure_reason
    o.notes = notes
    o.created = created or []
    return o


# Patches target the emails.webhook package because processing.py does
# `from emails.webhook import fetch_full_email, extract_email_text, extract_attachments`
_P_FETCH = 'emails.webhook.fetch_full_email'
_P_TEXT = 'emails.webhook.extract_email_text'
_P_ATT = 'emails.webhook.extract_attachments'
_P_PIPE_EMAIL = 'llm.pipeline.process_email'
_P_PIPE_TEXT = 'llm.pipeline.process_text'


@pytest.mark.django_db
class TestProcessInboundEmail:
    @patch(_P_PIPE_EMAIL)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_success_done(self, mock_fetch, mock_text, mock_att, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('done', created=[MagicMock()])
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE

    @patch(_P_PIPE_EMAIL)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_needs_review(self, mock_fetch, mock_text, mock_att, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('needs_review', notes='Conflicts found')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW

    @patch(_P_PIPE_EMAIL)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_no_events_note(self, mock_fetch, mock_text, mock_att, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('done', created=[], notes='')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert 'No events found' in job.notes

    def test_blocked_sender(self, user):
        from dashboard.models import Rule
        Rule.objects.create(user=user, rule_type='sender', pattern='@spam.com', action='block')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'evil@spam.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE
        assert 'blocked' in job.notes.lower()

    def test_duplicate_skipped(self, user):
        from dashboard.models import Event
        from datetime import datetime, timezone as dt_tz
        Event.objects.create(
            user=user, title='X', source_email_id='mid',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
        )
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE
        assert 'already processed' in job.notes.lower()

    @patch(_P_PIPE_EMAIL)
    @patch(_P_ATT, return_value=[])
    @patch(_P_TEXT, return_value='Hello')
    @patch(_P_FETCH, return_value={'id': 'e1'})
    def test_llm_error_fails(self, mock_fetch, mock_text, mock_att, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('failed', failure_reason='llm_error')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_FAILED
        assert job.failure_reason == 'llm_error'


@pytest.mark.django_db
class TestProcessUploadedFile:
    @patch(_P_PIPE_EMAIL)
    def test_done_with_events(self, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('done', created=[MagicMock()])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_uploaded_file(job.pk, user.pk, [['base64data', 'image/png', 'f.png']])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE

    @patch(_P_PIPE_EMAIL)
    def test_no_events_becomes_needs_review(self, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('done', created=[])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_uploaded_file(job.pk, user.pk, [['base64data', 'image/png', 'f.png']])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW
        assert 'No events found' in job.notes


@pytest.mark.django_db
class TestProcessTextAsUpload:
    @patch(_P_PIPE_TEXT)
    def test_done_with_events(self, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('done', created=[MagicMock()])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_text_as_upload(job.pk, user.pk, 'Exam on June 15')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE

    @patch(_P_PIPE_TEXT)
    def test_no_events_becomes_needs_review(self, mock_pipeline, user):
        mock_pipeline.return_value = _make_outcome('done', created=[])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_text_as_upload(job.pk, user.pk, 'random text')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW
        assert 'No events found' in job.notes
