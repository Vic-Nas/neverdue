"""
Integration tests using local Ollama to exercise the full extraction→validation
pipeline with a real LLM. Skipped if Ollama isn't running.

Run explicitly:  DEBUG=True python -m pytest tests/llm/test_ollama_integration.py -v
"""
import pytest
import requests
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

OLLAMA_URL = 'http://localhost:11434'
OLLAMA_MODEL = 'qwen2.5:7b'


def ollama_available():
    try:
        r = requests.get(f'{OLLAMA_URL}/api/tags', timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def ollama_call_api(**kwargs):
    """Drop-in replacement for call_api that routes to local Ollama."""
    system = kwargs.get('system', '')
    messages = kwargs.get('messages', [])
    user_text = messages[0]['content'] if messages else ''

    prompt = f"{system}\n\n{user_text}" if system else user_text

    resp = requests.post(f'{OLLAMA_URL}/api/generate', json={
        'model': OLLAMA_MODEL,
        'prompt': prompt,
        'stream': False,
        'options': {'temperature': 0, 'num_predict': 2000},
    }, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    # Build Anthropic-compatible response object
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=data['response'])]
    mock_msg.usage = MagicMock(
        input_tokens=data.get('prompt_eval_count', 0),
        output_tokens=data.get('eval_count', 0),
    )
    return mock_msg


skip_no_ollama = pytest.mark.skipif(
    not ollama_available(), reason='Ollama not running'
)


@skip_no_ollama
class TestTextExtraction:
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
        ev = events[0]
        if ev.get('recurrence_freq'):
            assert ev['recurrence_freq'] == 'WEEKLY'

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_validation_fixes_past_year(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            'Dentist appointment December 20 at 3pm.',
            language='English', user_timezone='America/Toronto',
        )
        if events:
            # Validation should ensure year >= current year
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
        ev = events[0]
        required_keys = {'title', 'start', 'end', 'description', 'status',
                         'category_hint', 'recurrence_freq', 'recurrence_until',
                         'concern', 'expires_at'}
        assert required_keys.issubset(ev.keys()), f"Missing keys: {required_keys - ev.keys()}"

    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    def test_french_language(self, mock_api):
        from llm.extractor.text import extract_events
        events, _, _ = extract_events(
            "L'examen final est le 15 juin 2026 à 14h dans la salle 101.",
            language='French', user_timezone='America/Toronto',
        )
        assert len(events) >= 1
