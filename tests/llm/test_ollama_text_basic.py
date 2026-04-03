"""Ollama integration: basic text extraction scenarios."""
import pytest
from unittest.mock import patch
from tests.llm.ollama_helpers import ollama_available, ollama_call_api

skip_no_ollama = pytest.mark.skipif(
    not ollama_available(), reason='Ollama not running',
)


@skip_no_ollama
class TestTextExtractionBasic:
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_simple_deadline(self, mock_api):
        from llm.extractor.text import extract_events
        events, inp, out = extract_events(
            'Final exam is on June 15 2026 at 2pm in room 101.',
            language='English', user_timezone='America/Toronto',
        )
        assert len(events) >= 1
        ev = events[0]
        assert 'title' in ev and ev['title']
        assert '2026-06' in ev['start']
        assert ev['status'] in ('active', 'pending')
        assert inp > 0 and out > 0

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_no_events(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            'Hey, just wanted to say hi. No plans this week.',
            language='English', user_timezone='UTC',
        )
        assert isinstance(events, list)
        assert len(events) == 0

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_multiple_events(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            'Math exam June 10 at 9am. Physics lab June 12 at 2pm. '
            'CS assignment due June 14 at midnight.',
            language='English', user_timezone='America/Toronto',
        )
        assert len(events) >= 2

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_recurring_event(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            'Weekly team meeting every Monday at 10am starting June 1 2026 until August 31 2026.',
            language='English', user_timezone='America/Toronto',
        )
        assert len(events) >= 1
        if events[0].get('recurrence_freq'):
            assert events[0]['recurrence_freq'] == 'WEEKLY'
