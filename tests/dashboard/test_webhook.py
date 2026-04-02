import pytest
from unittest.mock import patch
from dashboard.webhook import gcal_webhook, _sync_changed_events
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
