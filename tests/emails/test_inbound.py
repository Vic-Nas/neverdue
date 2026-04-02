import pytest
import json
from unittest.mock import patch, MagicMock
from django.test import RequestFactory
from emails.views import inbound
from emails.models import ScanJob


@pytest.mark.django_db
class TestInboundView:
    @patch('emails.views.verify_resend_signature', return_value=False)
    def test_invalid_signature(self, mock_verify):
        rf = RequestFactory()
        req = rf.post('/emails/inbound/', b'{}', content_type='application/json')
        resp = inbound(req)
        assert resp.status_code == 400

    @patch('emails.tasks.processing.process_inbound_email.defer')
    @patch('emails.views.verify_resend_signature', return_value=True)
    @patch('emails.views.get_user_from_recipient')
    def test_queues_job(self, mock_user, mock_verify, mock_defer, user):
        mock_user.return_value = user
        payload = json.dumps({
            'type': 'email.received',
            'data': {'to': [f'{user.username}@neverdue.ca'], 'from': 'a@b.com', 'email_id': 'eid'},
        })
        rf = RequestFactory()
        req = rf.post('/emails/inbound/', payload, content_type='application/json')
        resp = inbound(req)
        assert resp.status_code == 200
        assert ScanJob.objects.filter(user=user).exists()
        mock_defer.assert_called_once()

    @patch('emails.views.verify_resend_signature', return_value=True)
    def test_unknown_type_ok(self, mock_verify):
        payload = json.dumps({'type': 'email.bounced', 'data': {}})
        rf = RequestFactory()
        req = rf.post('/emails/inbound/', payload, content_type='application/json')
        resp = inbound(req)
        assert resp.status_code == 200
