import pytest
import json
from unittest.mock import patch
from datetime import datetime, timezone as dt_tz
from django.urls import reverse
from dashboard.models import Event


@pytest.fixture
def event(user):
    return Event.objects.create(
        user=user, title='Exam',
        start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
        end=datetime(2026, 6, 15, 11, tzinfo=dt_tz.utc),
    )


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
