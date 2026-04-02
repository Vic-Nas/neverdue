import pytest
from dashboard.models import Rule


@pytest.mark.django_db
class TestRule:
    def test_str_keyword(self, user):
        r = Rule.objects.create(
            user=user, rule_type=Rule.TYPE_KEYWORD,
            pattern='exam', action=Rule.ACTION_CATEGORIZE,
        )
        assert 'exam' in str(r)

    def test_str_prompt(self, user):
        r = Rule.objects.create(
            user=user, rule_type=Rule.TYPE_PROMPT,
            prompt_text='Always set priority high',
        )
        assert 'prompt:' in str(r)

    def test_sender_only_actions(self):
        assert Rule.ACTION_ALLOW in Rule.SENDER_ONLY_ACTIONS
        assert Rule.ACTION_BLOCK in Rule.SENDER_ONLY_ACTIONS
