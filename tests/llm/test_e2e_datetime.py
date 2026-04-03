"""E2E: exact date and time verification."""
import pytest
from unittest.mock import patch
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline, _to_toronto, TORONTO,
)


@skip_no_ollama
@pytest.mark.django_db
class TestExactDateTimeContent:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_explicit_date_and_time(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'My dentist appointment is June 20 2026 at 3pm.')
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.year == 2026 and local.month == 6 and local.day == 20
        assert local.hour == 15
        assert 'dentist' in events[0].title.lower()

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_multiple_events_correct_dates(self, _u, _api, _gc, user):
        _, events = _run_pipeline(
            user,
            'Math exam June 10 2026 at 9am. '
            'Physics lab June 12 2026 at 2pm. '
            'CS assignment due June 14 2026 at 5pm.',
        )
        assert len(events) >= 3
        by_day = {_to_toronto(e).day: _to_toronto(e) for e in sorted(events, key=lambda e: e.start)}
        assert 10 in by_day and 12 in by_day and 14 in by_day
        assert by_day[10].hour == 9
        assert by_day[12].hour == 14
        assert by_day[14].hour == 17

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_date_only_defaults_to_9am(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Project report due July 5 2026.')
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.month == 7 and local.day == 5
        assert local.hour == 9, f"Expected 09:00 default, got {local.hour}:00"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_end_after_start(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Team lunch June 18 2026 from 12pm to 1:30pm.')
        assert len(events) >= 1
        assert events[0].end > events[0].start
        local_end = events[0].end.astimezone(TORONTO)
        assert local_end.hour == 13 and local_end.minute == 30
