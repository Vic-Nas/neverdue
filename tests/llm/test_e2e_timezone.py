"""E2E: timezone offset verification (EDT vs EST)."""
import pytest
from datetime import timezone as dt_timezone
from unittest.mock import patch
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline, _to_toronto,
)


@skip_no_ollama
@pytest.mark.django_db
class TestTimezoneHandling:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_toronto_tz_summer(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Meeting July 1 2026 at 3pm.')
        assert len(events) >= 1
        ev = events[0]
        utc_hour = ev.start.astimezone(dt_timezone.utc).hour
        local_hour = _to_toronto(ev).hour
        assert local_hour == 15, f"Expected 15:00 local, got {local_hour}"
        assert utc_hour == 19, f"Expected 19:00 UTC (EDT offset), got {utc_hour}"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_toronto_tz_winter(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Meeting January 15 2027 at 3pm.')
        assert len(events) >= 1
        ev = events[0]
        utc_hour = ev.start.astimezone(dt_timezone.utc).hour
        local_hour = _to_toronto(ev).hour
        assert local_hour == 15, f"Expected 15:00 local, got {local_hour}"
        assert utc_hour == 20, f"Expected 20:00 UTC (EST offset), got {utc_hour}"
