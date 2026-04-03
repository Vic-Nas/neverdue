from unittest.mock import patch, MagicMock
from emails.webhook.resend import fetch_full_email, fetch_attachment_content


class TestFetchFullEmail:
    @patch('emails.webhook.resend.requests.get')
    def test_success(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'id': 'e1', 'text': 'hi'})
        assert fetch_full_email('e1') == {'id': 'e1', 'text': 'hi'}

    @patch('emails.webhook.resend.requests.get')
    def test_api_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=422)
        assert fetch_full_email('e1') == {}

    @patch('emails.webhook.resend.requests.get')
    def test_network_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException('timeout')
        assert fetch_full_email('e1') == {}

    @patch('emails.webhook.resend.settings')
    def test_no_api_key(self, mock_settings):
        mock_settings.RESEND_API_KEY = ''
        assert fetch_full_email('e1') == {}


class TestFetchAttachmentContent:
    @patch('emails.webhook.resend.requests.get')
    def test_success(self, mock_get):
        meta_resp = MagicMock(status_code=200, json=lambda: {
            'download_url': 'https://dl.example.com/file', 'content_type': 'image/png',
        })
        dl_resp = MagicMock(status_code=200, content=b'\x89PNG')
        mock_get.side_effect = [meta_resp, dl_resp]
        assert fetch_attachment_content('e1', 'a1') == (b'\x89PNG', 'image/png')

    @patch('emails.webhook.resend.requests.get')
    def test_meta_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500)
        assert fetch_attachment_content('e1', 'a1') is None

    @patch('emails.webhook.resend.requests.get')
    def test_no_download_url(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {})
        assert fetch_attachment_content('e1', 'a1') is None

    @patch('emails.webhook.resend.requests.get')
    def test_download_fails(self, mock_get):
        meta_resp = MagicMock(status_code=200, json=lambda: {
            'download_url': 'https://dl.example.com/file', 'content_type': 'image/png',
        })
        mock_get.side_effect = [meta_resp, MagicMock(status_code=500)]
        assert fetch_attachment_content('e1', 'a1') is None
