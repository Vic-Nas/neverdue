"""E2E: event content quality (titles, descriptions, language, scan limit)."""
import pytest
from unittest.mock import patch
from django.utils import timezone as tz
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline, _to_toronto,
)


@skip_no_ollama
@pytest.mark.django_db
class TestContentQuality:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_title_captures_event_nature(self, _u, _api, _gc, user):
        _, events = _run_pipeline(
            user, 'Board of directors meeting September 3 2026 at 4pm in the main conference room.',
        )
        assert len(events) >= 1
        title = events[0].title.lower()
        assert any(w in title for w in ('board', 'directors', 'meeting'))

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_description_has_context(self, _u, _api, _gc, user):
        _, events = _run_pipeline(
            user,
            'Dentist appointment June 22 2026 at 10am. '
            'Address: 123 Main St, Suite 400. Bring insurance card.',
        )
        assert len(events) >= 1
        desc = events[0].description.lower()
        assert any(w in desc for w in ('123 main', 'suite 400', 'insurance'))

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_french_content_correct(self, _u, _api, _gc, user):
        from accounts.models import User
        User.objects.filter(pk=user.pk).update(language='French')
        user.refresh_from_db()
        _, events = _run_pipeline(
            user, "Examen de mathématiques le 15 juin 2026 à 14h dans la salle A-200.",
        )
        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.month == 6 and local.day == 15 and local.hour == 14

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_scan_limit_blocks_without_hitting_llm(self, _u, mock_api, _gc, user):
        from dashboard.models import Event
        user.scan_reset_date = tz.now().date()
        user.monthly_scans = 30
        user.save(update_fields=['scan_reset_date', 'monthly_scans'])
        from llm.pipeline import process_text
        outcome = process_text(user, 'Meeting tomorrow at noon.')
        assert outcome.status == 'failed'
        assert outcome.failure_reason == 'scan_limit'
        assert Event.objects.filter(user=user).count() == 0
        mock_api.assert_not_called()
