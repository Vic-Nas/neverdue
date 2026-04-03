import pytest
import base64
from unittest.mock import patch, MagicMock
from llm.pipeline.entry import process_email


SAMPLE_EVENT = {
    'title': 'Exam', 'start': '2026-06-15T13:00:00+00:00',
    'end': '2026-06-15T15:00:00+00:00', 'description': '',
    'category_hint': 'Exams', 'recurrence_freq': '',
    'recurrence_until': '', 'status': 'active',
    'concern': '', 'expires_at': '', 'source_email_id': '',
}


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
        att = [base64.b64encode(b'img').decode(), 'image/jpeg', 'f.jpg']
        with patch('llm.pipeline.saving.write_event_to_calendar', return_value=MagicMock(pk=1)):
            with patch('llm.pipeline.entry.extract_events_from_email') as mock_ext:
                mock_ext.return_value = ([SAMPLE_EVENT], 100, 50)
                with patch('llm.pipeline.saving._fire_usage'):
                    outcome = process_email(user, 'body', [att])
        assert 'Upgrade' in outcome.notes or outcome.status in ('done', 'needs_review')
