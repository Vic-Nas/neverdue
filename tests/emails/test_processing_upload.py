import pytest
from unittest.mock import patch, MagicMock
from emails.models import ScanJob
from emails.tasks.processing import process_uploaded_file, process_text_as_upload


def _make_outcome(status='done', created=None):
    o = MagicMock()
    o.status, o.failure_reason, o.notes = status, '', ''
    o.created = created or []
    return o


@pytest.mark.django_db
class TestProcessUploadedFile:
    @patch('llm.pipeline.process_email')
    def test_done_with_events(self, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('done', [MagicMock()])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_uploaded_file(job.pk, user.pk, [['base64data', 'image/png', 'f.png']])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE

    @patch('llm.pipeline.process_email')
    def test_no_events_becomes_needs_review(self, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('done', [])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_uploaded_file(job.pk, user.pk, [['base64data', 'image/png', 'f.png']])
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW
        assert 'No events found' in job.notes


@pytest.mark.django_db
class TestProcessTextAsUpload:
    @patch('llm.pipeline.process_text')
    def test_done_with_events(self, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('done', [MagicMock()])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_text_as_upload(job.pk, user.pk, 'Exam on June 15')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE

    @patch('llm.pipeline.process_text')
    def test_no_events_becomes_needs_review(self, mock_pipe, user):
        mock_pipe.return_value = _make_outcome('done', [])
        job = ScanJob.objects.create(user=user, source='upload', status='queued')
        process_text_as_upload(job.pk, user.pk, 'random text')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW
        assert 'No events found' in job.notes
