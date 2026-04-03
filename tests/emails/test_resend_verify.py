from unittest.mock import patch
from emails.webhook.resend import verify_resend_signature


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
