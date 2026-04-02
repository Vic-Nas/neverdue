import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone as dt_tz
from emails.models import ScanJob
from emails.tasks.reprocess import reprocess_events
from dashboard.models import Event


def _make_outcome(status='done', failure_reason='', notes='', created=None):
    o = MagicMock()
    o.status = status
    o.failure_reason = failure_reason
    o.notes = notes
    o.created = created or []
    return o


@pytest.mark.django_db
class TestReprocessEvents:
    def _make_pending(self, user, job):
        return Event.objects.create(
            user=user, title='Exam', status='pending',
            start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 15, 10, tzinfo=dt_tz.utc),
            scan_job=job, pending_concern='Ambiguous date',
        )

    @patch('llm.pipeline.saving.write_event_to_calendar', return_value=MagicMock(pk=99))
    @patch('llm.pipeline.saving._fire_usage')
    @patch('llm.pipeline.entry.extract_events')
    def test_reprocess_with_prompt_replaces(self, mock_extract, mock_fire, mock_write, user):
        mock_extract.return_value = ([{
            'title': 'Exam', 'start': '2026-06-15T09:00:00',
            'end': '2026-06-15T10:00:00', 'description': '',
            'category_hint': '', 'recurrence_freq': '', 'recurrence_until': '',
            'status': 'active', 'concern': '', 'expires_at': '', 'source_email_id': '',
        }], 100, 50)
        job = ScanJob.objects.create(user=user, source='upload', status='needs_review')
        evt = self._make_pending(user, job)
        reprocess_events(user.pk, [evt.pk], 'The exam is June 15 at 9 AM', job.pk)
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE
        assert not Event.objects.filter(pk=evt.pk).exists()

    def test_empty_prompt_clears(self, user):
        job = ScanJob.objects.create(user=user, source='upload', status='needs_review')
        evt = self._make_pending(user, job)
        reprocess_events(user.pk, [evt.pk], '', job.pk)
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE
        assert 'cleared' in job.notes.lower()
        assert not Event.objects.filter(pk=evt.pk).exists()

    def test_missing_job(self, user):
        # Should return silently, not crash
        reprocess_events(user.pk, [], 'fix it', 99999)

    def test_missing_user(self, user):
        job = ScanJob.objects.create(user=user, source='upload', status='needs_review')
        reprocess_events(99999, [], 'fix it', job.pk)
        job.refresh_from_db()
        # user_id mismatch: ScanJob.get(pk, user_id=99999) raises DoesNotExist → silent return
        assert job.status == ScanJob.STATUS_NEEDS_REVIEW
