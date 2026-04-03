import pytest
from unittest.mock import patch, MagicMock
from llm.pipeline.entry import process_text
from llm.extractor.client import LLMAPIError
from dashboard.writer import GCalUnavailableError


SAMPLE_EVENT = {
    'title': 'Exam', 'start': '2026-06-15T13:00:00+00:00',
    'end': '2026-06-15T15:00:00+00:00', 'description': '',
    'category_hint': 'Exams', 'recurrence_freq': '',
    'recurrence_until': '', 'status': 'active',
    'concern': '', 'expires_at': '', 'source_email_id': '',
}


@pytest.mark.django_db
class TestProcessText:
    @patch('llm.pipeline.saving.write_event_to_calendar', return_value=MagicMock(pk=1))
    @patch('llm.pipeline.saving._fire_usage')
    @patch('llm.pipeline.entry.extract_events')
    def test_success(self, mock_extract, mock_fire, mock_write, user):
        mock_extract.return_value = ([SAMPLE_EVENT], 100, 50)
        outcome = process_text(user, 'Exam on June 15')
        assert outcome.status == 'done'
        assert len(outcome.created) == 1

    @patch('llm.pipeline.entry.extract_events', side_effect=ValueError('fail'))
    def test_llm_error(self, mock_extract, user):
        outcome = process_text(user, 'text')
        assert outcome.status == 'failed'
        assert outcome.failure_reason == 'llm_error'

    @patch('llm.pipeline.entry.extract_events', side_effect=LLMAPIError('quota exceeded'))
    def test_api_error_fails_with_notes(self, mock_extract, user):
        outcome = process_text(user, 'text')
        assert outcome.status == 'failed'
        assert outcome.failure_reason == 'llm_error'
        assert 'quota exceeded' in outcome.notes

    def test_scan_limit(self, user):
        from django.utils import timezone as tz
        user.monthly_scans = 30
        user.scan_reset_date = tz.now().date()
        user.save()
        outcome = process_text(user, 'text')
        assert outcome.failure_reason == 'scan_limit'

    @patch('llm.pipeline.saving._save_events', side_effect=GCalUnavailableError('no token'))
    @patch('llm.pipeline.saving._fire_usage')
    @patch('llm.pipeline.entry.extract_events')
    def test_gcal_disconnected(self, mock_ext, _fire, _save, user):
        mock_ext.return_value = ([SAMPLE_EVENT], 100, 50)
        outcome = process_text(user, 'text')
        assert outcome.status == 'failed'
        assert outcome.failure_reason == 'gcal_disconnected'
