"""E2E: recurrence verification."""
import pytest
from unittest.mock import patch
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline,
)


@skip_no_ollama
@pytest.mark.django_db
class TestRecurrence:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_weekly_recurrence(self, _u, _api, _gc, user):
        _, events = _run_pipeline(
            user, 'Weekly team standup every Monday at 10am from June 1 2026 to August 31 2026.',
        )
        assert len(events) >= 1
        ev = events[0]
        assert ev.recurrence_freq == 'WEEKLY', f"Expected WEEKLY, got {ev.recurrence_freq!r}"
        assert ev.recurrence_until is not None
        assert ev.recurrence_until.year == 2026 and ev.recurrence_until.month == 8

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_recurrence_on_single_event(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Company BBQ on July 4 2026 at 5pm.')
        assert len(events) >= 1
        assert not events[0].recurrence_freq
