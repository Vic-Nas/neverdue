"""Ollama integration: validation, output structure, and language."""
import pytest
from unittest.mock import patch
from tests.llm.ollama_helpers import ollama_available, ollama_call_api

skip_no_ollama = pytest.mark.skipif(
    not ollama_available(), reason='Ollama not running',
)


@skip_no_ollama
class TestTextExtractionExtra:
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_validation_fixes_past_year(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            'Dentist appointment December 20 at 3pm.',
            language='English', user_timezone='America/Toronto',
        )
        if events:
            year = int(events[0]['start'][:4])
            assert year >= 2026

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_json_output_structure(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            'Project deadline March 20 2027 at 5pm.',
            language='English', user_timezone='UTC',
        )
        assert len(events) >= 1
        required_keys = {
            'title', 'start', 'end', 'description', 'status',
            'category_hint', 'recurrence_freq', 'recurrence_until',
            'concern', 'expires_at',
        }
        assert required_keys.issubset(events[0].keys())

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_french_language(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            "L'examen final est le 15 juin 2026 à 14h dans la salle 101.",
            language='French', user_timezone='America/Toronto',
        )
        assert len(events) >= 1
