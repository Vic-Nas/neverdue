"""E2E: user prompt instructions override LLM inference."""
import pytest
from unittest.mock import patch
from tests.llm.e2e_helpers import (
    skip_no_ollama, ollama_call_api, _fake_svc, _run_pipeline,
)


@skip_no_ollama
@pytest.mark.django_db
class TestUserInstructions:
    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_user_instruction_forces_category(self, _u, _api, _gc, user):
        from dashboard.models import Rule
        Rule.objects.create(
            user=user, rule_type=Rule.TYPE_PROMPT,
            prompt_text='Always set the category_hint to "Work" for any event.',
        )
        _, events = _run_pipeline(user, 'Sprint planning June 25 2026 at 11am.')
        assert len(events) >= 1
        cat = events[0].category
        assert cat is not None
        assert cat.name.lower() == 'work', f"Expected 'Work' category, got '{cat.name}'"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_user_instruction_title_format(self, _u, _api, _gc, user):
        from dashboard.models import Rule
        Rule.objects.create(
            user=user, rule_type=Rule.TYPE_PROMPT,
            prompt_text='Prefix every event title with "[UNI]".',
        )
        _, events = _run_pipeline(user, 'Linear algebra exam June 20 2026 at 9am.')
        assert len(events) >= 1
        assert events[0].title.startswith('[UNI]'), f"Title missing prefix: {events[0].title!r}"
