import pytest
import json
from django.urls import reverse
from dashboard.models import Category, Rule


@pytest.mark.django_db
class TestRulesView:
    def test_renders(self, auth_client):
        resp = auth_client.get(reverse('dashboard:rules'))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestRuleAdd:
    def test_add_keyword_rule(self, auth_client, user):
        cat = Category.objects.create(user=user, name='Class')
        resp = auth_client.post(
            reverse('dashboard:rule_add'),
            json.dumps({
                'rule_type': 'keyword', 'pattern': 'exam',
                'action': 'categorize', 'category_id': cat.pk,
            }),
            content_type='application/json',
        )
        assert resp.json()['ok']
        assert Rule.objects.filter(user=user, pattern='exam').exists()

    def test_add_prompt_rule(self, auth_client):
        resp = auth_client.post(
            reverse('dashboard:rule_add'),
            json.dumps({'rule_type': 'prompt', 'prompt_text': 'Set priority high'}),
            content_type='application/json',
        )
        assert resp.json()['ok']

    def test_missing_type_rejected(self, auth_client):
        resp = auth_client.post(
            reverse('dashboard:rule_add'),
            json.dumps({'rule_type': '', 'pattern': 'x'}),
            content_type='application/json',
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestRuleDelete:
    def test_delete(self, auth_client, user):
        r = Rule.objects.create(user=user, rule_type='keyword', pattern='x', action='discard')
        resp = auth_client.post(reverse('dashboard:rule_delete', args=[r.pk]))
        assert resp.json()['ok']
