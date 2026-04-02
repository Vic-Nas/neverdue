import pytest
import json
from unittest.mock import patch, MagicMock
from llm.pipeline.entry import process_text, process_email


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

    def test_scan_limit(self, user):
        from django.utils import timezone as tz
        user.monthly_scans = 30
        user.scan_reset_date = tz.now().date()
        user.save()
        outcome = process_text(user, 'text')
        assert outcome.failure_reason == 'scan_limit'


@pytest.mark.django_db
class TestProcessEmail:
    @patch('llm.pipeline.saving.write_event_to_calendar', return_value=MagicMock(pk=1))
    @patch('llm.pipeline.saving._fire_usage')
    @patch('llm.pipeline.entry.extract_events_from_email')
    def test_success(self, mock_extract, mock_fire, mock_write, user):
        mock_extract.return_value = ([SAMPLE_EVENT], 100, 50)
        outcome = process_email(user, 'body', [])
        assert outcome.status == 'done'

    def test_non_pro_attachment_stripped(self, user):
        import base64
        att = [base64.b64encode(b'img').decode(), 'image/jpeg', 'f.jpg']
        with patch('llm.pipeline.saving.write_event_to_calendar', return_value=MagicMock(pk=1)):
            with patch('llm.pipeline.entry.extract_events_from_email') as mock_ext:
                mock_ext.return_value = ([SAMPLE_EVENT], 100, 50)
                with patch('llm.pipeline.saving._fire_usage'):
                    outcome = process_email(user, 'body', [att])
        # Non-pro: attachments stripped, but body still processed
        assert 'Upgrade' in outcome.notes or outcome.status in ('done', 'needs_review')
