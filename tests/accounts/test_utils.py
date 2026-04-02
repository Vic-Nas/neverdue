import pytest
from unittest.mock import patch, MagicMock
from accounts.utils import get_valid_token, revoke_google_token


@pytest.mark.django_db
class TestGetValidToken:
    def test_raises_without_refresh_token(self, user):
        user.google_refresh_token = None
        with pytest.raises(ValueError, match='No refresh token'):
            get_valid_token(user)

    @patch('accounts.utils.Credentials')
    @patch('accounts.utils.Request')
    def test_refreshes_expired_token(self, mock_req, mock_creds_cls, user):
        user.google_refresh_token = 'refresh'
        user.google_calendar_token = 'old'
        user.token_expiry = None
        user.save()
        creds = MagicMock(valid=False, token='new-token')
        mock_creds_cls.return_value = creds
        token = get_valid_token(user)
        creds.refresh.assert_called_once()
        assert token == 'new-token'


@pytest.mark.django_db
class TestRevokeGoogleToken:
    @patch('accounts.utils.http_requests.post')
    def test_clears_tokens_on_success(self, mock_post, user):
        mock_post.return_value = MagicMock(status_code=200)
        user.google_refresh_token = 'refresh'
        user.google_calendar_token = 'access'
        user.save()
        revoke_google_token(user)
        user.refresh_from_db()
        assert user.google_calendar_token is None
        assert user.google_refresh_token is None

    @patch('accounts.utils.http_requests.post', side_effect=Exception('net'))
    def test_clears_tokens_on_failure(self, mock_post, user):
        user.google_refresh_token = 'refresh'
        user.save()
        revoke_google_token(user)
        user.refresh_from_db()
        assert user.google_refresh_token is None
