import pytest
import json
from unittest.mock import MagicMock
from llm.extractor.validation import parse_and_validate, _validate_event
import zoneinfo


def _make_message(events_json: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=events_json)]
    return msg


class TestParseAndValidate:
    def test_valid(self):
        tz = zoneinfo.ZoneInfo('UTC')
        events = json.dumps([{
            'title': 'Test', 'start': '2026-06-01T09:00:00',
            'end': '2026-06-01T10:00:00', 'description': '',
            'category_hint': '', 'recurrence_freq': '',
            'recurrence_until': '', 'status': 'active',
            'concern': '', 'expires_at': '',
        }])
        result = parse_and_validate(_make_message(events), tz)
        assert len(result) == 1
        assert result[0]['title'] == 'Test'

    def test_markdown_stripped(self):
        tz = zoneinfo.ZoneInfo('UTC')
        events = json.dumps([{
            'title': 'Test', 'start': '2026-06-01T09:00:00',
            'end': '2026-06-01T10:00:00', 'description': '',
            'category_hint': '', 'recurrence_freq': '',
            'recurrence_until': '', 'status': 'active',
            'concern': '', 'expires_at': '',
        }])
        raw = f'```json\n{events}\n```'
        result = parse_and_validate(_make_message(raw), tz)
        assert len(result) == 1

    def test_invalid_json_raises(self):
        tz = zoneinfo.ZoneInfo('UTC')
        with pytest.raises(ValueError, match='invalid JSON'):
            parse_and_validate(_make_message('not json'), tz)

    def test_non_list_raises(self):
        tz = zoneinfo.ZoneInfo('UTC')
        with pytest.raises(ValueError, match='non-list'):
            parse_and_validate(_make_message('{"a": 1}'), tz)

    def test_missing_title_skipped(self):
        tz = zoneinfo.ZoneInfo('UTC')
        events = json.dumps([{
            'title': '', 'start': '2026-06-01T09:00:00',
            'end': '2026-06-01T10:00:00',
        }])
        result = parse_and_validate(_make_message(events), tz)
        assert len(result) == 0


class TestPastYearFix:
    def test_bumps_past_year(self):
        tz = zoneinfo.ZoneInfo('America/Toronto')
        event = {
            'title': 'Old', 'start': '2025-09-01T09:00:00',
            'end': '2025-09-01T10:00:00', 'status': 'pending',
            'concern': 'Past date', 'expires_at': '',
            'recurrence_freq': '', 'recurrence_until': '',
            'category_hint': '', 'description': '',
        }
        result = _validate_event(event, tz)
        assert '2026' in result['start']
        # year fix promotes pending → active
        assert result['status'] == 'active'

    def test_keeps_current_year(self):
        tz = zoneinfo.ZoneInfo('UTC')
        event = {
            'title': 'Future', 'start': '2026-12-01T09:00:00',
            'end': '2026-12-01T10:00:00', 'status': 'active',
            'concern': '', 'expires_at': '',
            'recurrence_freq': '', 'recurrence_until': '',
            'category_hint': '', 'description': '',
        }
        result = _validate_event(event, tz)
        assert result['status'] == 'active'


class TestRecurrenceValidation:
    def test_too_long_duration_strips_freq(self):
        tz = zoneinfo.ZoneInfo('UTC')
        event = {
            'title': 'Long', 'start': '2026-06-01T09:00:00',
            'end': '2026-06-10T09:00:00',  # 9 days > DAILY
            'recurrence_freq': 'DAILY', 'recurrence_until': '2026-12-31',
            'status': 'active', 'concern': '', 'expires_at': '',
            'category_hint': '', 'description': '',
        }
        result = _validate_event(event, tz)
        assert result['recurrence_freq'] == ''

    def test_invalid_freq_stripped(self):
        tz = zoneinfo.ZoneInfo('UTC')
        event = {
            'title': 'Bad', 'start': '2026-06-01T09:00:00',
            'end': '2026-06-01T10:00:00',
            'recurrence_freq': 'HOURLY', 'recurrence_until': '',
            'status': 'active', 'concern': '', 'expires_at': '',
            'category_hint': '', 'description': '',
        }
        result = _validate_event(event, tz)
        assert result['recurrence_freq'] == ''
