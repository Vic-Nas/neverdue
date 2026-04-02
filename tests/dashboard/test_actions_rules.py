import pytest
import json
from unittest.mock import patch
from datetime import datetime, timezone as dt_tz
from django.urls import reverse
from dashboard.models import Event, Category, Rule


@pytest.fixture
def event(user):
    return Event.objects.create(
        user=user, title='Exam',
        start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
        end=datetime(2026, 6, 15, 11, tzinfo=dt_tz.utc),
    )


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
            json.dumps({
                'rule_type': 'prompt', 'prompt_text': 'Set priority high',
            }),
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


@pytest.mark.django_db
class TestExportEvents:
    def test_export_all(self, auth_client, event):
        resp = auth_client.get(reverse('dashboard:events_export') + '?ids=all')
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'text/calendar'

    def test_export_no_ids(self, auth_client):
        resp = auth_client.get(reverse('dashboard:events_export'))
        assert resp.status_code == 400


@pytest.mark.django_db
class TestEventPromptEdit:
    @patch('emails.tasks.processing.process_text_as_upload.defer')
    def test_creates_job(self, mock_defer, auth_client, event):
        resp = auth_client.post(
            reverse('dashboard:event_prompt_edit', args=[event.pk]),
            json.dumps({'prompt': 'Change time to 3 PM'}),
            content_type='application/json',
        )
        assert resp.json()['ok']
        mock_defer.assert_called_once()
