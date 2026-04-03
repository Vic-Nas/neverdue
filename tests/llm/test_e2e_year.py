"""E2E: year inference when no year is given."""
import pytest
from unittest.mock import patch
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline, _to_toronto,
)


@skip_no_ollama
@pytest.mark.django_db
class TestYearInference:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_year_future_month(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Conference on November 15 at 10am.')
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.year == 2026 and local.month == 11 and local.day == 15

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_year_past_month(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Dentist on January 10 at 2pm.')
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.year >= 2026
        assert local.month == 1 and local.day == 10 and local.hour == 14

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_explicit_past_year_gets_fixed(self, _u, _api, _gc, user):
        _, events = _run_pipeline(user, 'Renew passport — was due March 5 2024 at noon.')
        if events:
            local = _to_toronto(events[0])
            assert local.year >= 2026, f"Past year {local.year} not corrected"
