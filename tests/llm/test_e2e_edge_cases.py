"""E2E: tricky inputs that LLMs commonly get wrong."""
import pytest
from unittest.mock import patch
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline, _to_toronto,
)


@skip_no_ollama
@pytest.mark.django_db
class TestEdgeCases:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_midnight_is_correct_day(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Assignment due June 14 2026 at midnight.')
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.month == 6
        valid = (local.day == 14) or (local.day == 15 and local.hour == 0)
        assert valid, f"Expected June 14 or June 15 00:00, got June {local.day} {local.hour}:00"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_events_in_chitchat(self, _u, _api, _gc, user):
        _, events = _run_pipeline(
            user, 'Hey! Hope you had a great weekend. Let me know if you want to grab coffee sometime.',
        )
        assert len(events) == 0

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_am_pm_not_confused(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Morning class August 10 2026 at 8:30am.')
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.hour == 8, f"Expected 8 (AM), got {local.hour}"
        assert local.minute == 30
