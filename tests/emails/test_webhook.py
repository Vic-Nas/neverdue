import pytest
from unittest.mock import patch, MagicMock

from emails.webhook.parsing import extract_email_text, extract_attachments
from emails.webhook.users import get_user_from_recipient, RESERVED_USERNAMES
from emails.webhook.resend import verify_resend_signature


class TestExtractEmailText:
    def test_prefers_text(self):
        assert extract_email_text({'text': 'Hello', 'html': '<b>Hi</b>'}) == 'Hello'

    def test_falls_back_to_html(self):
        result = extract_email_text({'html': '<p>Content</p>'})
        assert 'Content' in result

    def test_empty(self):
        assert extract_email_text({}) == ''


class TestExtractAttachments:
    @patch('emails.webhook.parsing.fetch_attachment_content')
    def test_fetches_supported(self, mock_fetch):
        mock_fetch.return_value = (b'data', 'image/jpeg')
        result = extract_attachments({
            'id': 'e1',
            'attachments': [{'id': 'a1', 'content_type': 'image/jpeg', 'filename': 'f.jpg'}],
        })
        assert len(result) == 1
        assert result[0][1] == 'image/jpeg'

    def test_no_attachments(self):
        assert extract_attachments({'id': 'e1'}) == []


@pytest.mark.django_db
class TestGetUserFromRecipient:
    def test_found(self, user):
        result = get_user_from_recipient(f'{user.username}@neverdue.ca')
        assert result.pk == user.pk

    def test_reserved(self):
        assert get_user_from_recipient('admin@neverdue.ca') is None

    def test_not_found(self):
        assert get_user_from_recipient('nobody@neverdue.ca') is None

    def test_dotted_local(self, user):
        result = get_user_from_recipient(f'{user.username}.extra@neverdue.ca')
        assert result.pk == user.pk


class TestReservedUsernames:
    def test_common_reserved(self):
        for name in ('admin', 'support', 'billing', 'api', 'noreply'):
            assert name in RESERVED_USERNAMES


class TestVerifyResendSignature:
    @patch('emails.webhook.resend.Webhook')
    def test_valid(self, mock_wh_cls, settings):
        settings.RESEND_WEBHOOK_SECRET = 'secret'
        mock_wh_cls.return_value.verify.return_value = True
        result = verify_resend_signature(b'body', {
            'HTTP_SVIX_ID': 'id', 'HTTP_SVIX_TIMESTAMP': 'ts',
            'HTTP_SVIX_SIGNATURE': 'sig',
        })
        assert result is True
