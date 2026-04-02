import pytest
from unittest.mock import patch, MagicMock
from emails.webhook.resend import fetch_full_email, fetch_attachment_content, verify_resend_signature


class TestFetchFullEmail:
    @patch('emails.webhook.resend.requests.get')
    def test_success(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'id': 'e1', 'text': 'hi'})
        result = fetch_full_email('e1')
        assert result == {'id': 'e1', 'text': 'hi'}

    @patch('emails.webhook.resend.requests.get')
    def test_api_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=422)
        result = fetch_full_email('e1')
        assert result == {}

    @patch('emails.webhook.resend.requests.get')
    def test_network_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException('timeout')
        result = fetch_full_email('e1')
        assert result == {}

    @patch('emails.webhook.resend.settings')
    def test_no_api_key(self, mock_settings):
        mock_settings.RESEND_API_KEY = ''
        result = fetch_full_email('e1')
        assert result == {}


class TestFetchAttachmentContent:
    @patch('emails.webhook.resend.requests.get')
    def test_success(self, mock_get):
        meta_resp = MagicMock(status_code=200, json=lambda: {
            'download_url': 'https://dl.example.com/file',
            'content_type': 'image/png',
        })
        dl_resp = MagicMock(status_code=200, content=b'\x89PNG')
        mock_get.side_effect = [meta_resp, dl_resp]
        result = fetch_attachment_content('e1', 'a1')
        assert result == (b'\x89PNG', 'image/png')

    @patch('emails.webhook.resend.requests.get')
    def test_meta_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500)
        result = fetch_attachment_content('e1', 'a1')
        assert result is None

    @patch('emails.webhook.resend.requests.get')
    def test_no_download_url(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {})
        result = fetch_attachment_content('e1', 'a1')
        assert result is None

    @patch('emails.webhook.resend.requests.get')
    def test_download_fails(self, mock_get):
        meta_resp = MagicMock(status_code=200, json=lambda: {
            'download_url': 'https://dl.example.com/file',
            'content_type': 'image/png',
        })
        dl_resp = MagicMock(status_code=500)
        mock_get.side_effect = [meta_resp, dl_resp]
        result = fetch_attachment_content('e1', 'a1')
        assert result is None


class TestVerifyResendSignature:
    @patch('emails.webhook.resend.Webhook')
    def test_valid(self, mock_wh_cls):
        mock_wh_cls.return_value.verify.return_value = True
        assert verify_resend_signature(b'payload', {
            'HTTP_SVIX_ID': 'id', 'HTTP_SVIX_TIMESTAMP': 'ts', 'HTTP_SVIX_SIGNATURE': 'sig',
        }) is True

    @patch('emails.webhook.resend.Webhook')
    def test_invalid_signature(self, mock_wh_cls):
        from svix.webhooks import WebhookVerificationError
        mock_wh_cls.return_value.verify.side_effect = WebhookVerificationError('bad')
        assert verify_resend_signature(b'payload', {
            'HTTP_SVIX_ID': 'id', 'HTTP_SVIX_TIMESTAMP': 'ts', 'HTTP_SVIX_SIGNATURE': 'sig',
        }) is False

    @patch('emails.webhook.resend.settings')
    def test_no_secret(self, mock_settings):
        mock_settings.RESEND_WEBHOOK_SECRET = ''
        assert verify_resend_signature(b'payload', {}) is False
