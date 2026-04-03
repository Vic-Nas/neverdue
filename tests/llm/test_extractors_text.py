import pytest
import json
from unittest.mock import patch, MagicMock
from llm.extractor.text import extract_events
from llm.extractor.client import LLMAPIError


def _mock_api_response(events):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(events))]
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    return msg


SAMPLE_EVENT = {
    'title': 'Exam', 'start': '2026-06-15T09:00:00',
    'end': '2026-06-15T11:00:00', 'description': 'Final exam',
    'category_hint': 'Exams', 'recurrence_freq': '',
    'recurrence_until': '', 'status': 'active',
    'concern': '', 'expires_at': '',
}


class TestExtractText:
    @patch('llm.extractor.text.call_api')
    def test_returns_events(self, mock_api):
        mock_api.return_value = _mock_api_response([SAMPLE_EVENT])
        events, inp, out = extract_events('Exam on June 15')
        assert len(events) == 1
        assert events[0]['title'] == 'Exam'
        assert inp == 100

    @patch('llm.extractor.text.call_api', side_effect=LLMAPIError('quota exceeded'))
    def test_raises_on_api_error(self, mock_api):
        with pytest.raises(LLMAPIError):
            extract_events('text')
