import pytest
from dashboard.models import Category, Rule
from llm.resolver import (
    resolve_category, collect_prompt_injections,
    _infer_priority, DISCARD,
)


class TestInferPriority:
    def test_exam_is_urgent(self):
        assert _infer_priority('Final Exam') == 4

    def test_course_is_medium(self):
        assert _infer_priority('CS101 Lecture') == 2

    def test_unknown_defaults(self):
        assert _infer_priority('Party') == 2


@pytest.mark.django_db
class TestCollectPromptInjections:
    def test_no_rules(self, user):
        assert collect_prompt_injections(user) == ''

    def test_global_rule(self, user):
        Rule.objects.create(user=user, rule_type='prompt', prompt_text='Set high', pattern='')
        result = collect_prompt_injections(user)
        assert 'Set high' in result

    def test_sender_scoped(self, user):
        Rule.objects.create(user=user, rule_type='prompt', prompt_text='Only for prof', pattern='@uni.edu')
        assert collect_prompt_injections(user, 'prof@uni.edu') != ''
        assert collect_prompt_injections(user, 'other@gmail.com') == ''


@pytest.mark.django_db
class TestResolveCategory:
    def test_keyword_categorize(self, user):
        cat = Category.objects.create(user=user, name='School')
        Rule.objects.create(user=user, rule_type='keyword', pattern='exam', action='categorize', category=cat)
        result = resolve_category(user, {'title': 'Final Exam', 'description': ''})
        assert result == cat

    def test_keyword_discard(self, user):
        Rule.objects.create(user=user, rule_type='keyword', pattern='spam', action='discard')
        result = resolve_category(user, {'title': 'Spam event', 'description': ''})
        assert result is DISCARD

    def test_hint_creates_category(self, user):
        result = resolve_category(user, {
            'title': 'Test', 'description': '', 'category_hint': 'homework',
        })
        assert result is not None
        assert result.name == 'Homework'

    def test_hint_matches_existing(self, user):
        cat = Category.objects.create(user=user, name='Homework')
        result = resolve_category(user, {
            'title': 'Test', 'description': '', 'category_hint': 'homework',
        })
        assert result.pk == cat.pk

    def test_no_hint_returns_none(self, user):
        result = resolve_category(user, {'title': 'Test', 'description': ''})
        assert result is None
