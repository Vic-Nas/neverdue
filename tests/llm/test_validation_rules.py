import zoneinfo
from llm.extractor.validation import _validate_event


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
            'end': '2026-06-10T09:00:00',
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
