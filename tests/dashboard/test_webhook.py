import pytest
from datetime import datetime, timezone as dt_tz
from unittest.mock import patch, MagicMock
from dashboard.webhook import gcal_webhook, _sync_changed_events
from dashboard.models import Event
from django.test import RequestFactory


@pytest.mark.django_db
class TestGcalWebhook:
    def _req(self, **headers):
        rf = RequestFactory()
        req = rf.post('/dashboard/gcal/webhook/', content_type='application/json')
        for k, v in headers.items():
            req.META[f'HTTP_{k.upper().replace("-", "_")}'] = v
        return req

    def test_sync_state_returns_200(self):
        req = self._req(**{'X-Goog-Channel-ID': 'ch1', 'X-Goog-Resource-State': 'sync'})
        resp = gcal_webhook(req)
        assert resp.status_code == 200

    @patch('dashboard.webhook._sync_changed_events')
    def test_exists_state_syncs(self, mock_sync, user):
        user.gcal_channel_id = 'ch1'
        user.save()
        req = self._req(**{
            'X-Goog-Channel-ID': 'ch1',
            'X-Goog-Resource-State': 'exists',
        })
        resp = gcal_webhook(req)
        assert resp.status_code == 200
        mock_sync.assert_called_once()


@pytest.mark.django_db
class TestSyncChangedEvents:
    @patch('dashboard.gcal.client._service')
    def test_updates_color_and_link(self, mock_svc, user):
        event = Event.objects.create(
            user=user, title='Exam', google_event_id='gcal_1',
            start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 15, 10, tzinfo=dt_tz.utc),
            color='', gcal_link='',
        )
        svc = MagicMock()
        svc.events().list().execute.return_value = {
            'items': [{'id': 'gcal_1', 'colorId': '7', 'htmlLink': 'https://cal.google.com/e/1'}],
        }
        mock_svc.return_value = svc
        _sync_changed_events(user)
        event.refresh_from_db()
        assert event.color == '7'
        assert event.gcal_link == 'https://cal.google.com/e/1'

    @patch('dashboard.gcal.client._service')
    def test_ignores_unknown_events(self, mock_svc, user):
        svc = MagicMock()
        svc.events().list().execute.return_value = {
            'items': [{'id': 'unknown_gcal_id', 'colorId': '3'}],
        }
        mock_svc.return_value = svc
        _sync_changed_events(user)  # Should not raise

    @patch('dashboard.gcal.client._service', side_effect=Exception('no token'))
    def test_token_failure(self, mock_svc, user):
        _sync_changed_events(user)  # Should not raise
