import pytest
from unittest.mock import patch
from django.urls import reverse


@pytest.mark.django_db
class TestSaveToGcalPreference:
    def test_unchecked_saves_false(self, auth_client, user):
        auth_client.post(reverse('accounts:preferences'), {
            'language': 'English', 'timezone': 'America/Toronto',
        })
        user.refresh_from_db()
        assert user.save_to_gcal is False

    def test_checked_saves_true(self, auth_client, user):
        user.google_refresh_token = 'fake_token'
        user.save(update_fields=['google_refresh_token'])
        auth_client.post(reverse('accounts:preferences'), {
            'language': 'English', 'timezone': 'America/Toronto',
            'save_to_gcal': 'on',
        })
        user.refresh_from_db()
        assert user.save_to_gcal is True

    def test_checked_ignored_without_token(self, auth_client, user):
        """Can't enable sync without a Google token."""
        auth_client.post(reverse('accounts:preferences'), {
            'language': 'English', 'timezone': 'America/Toronto',
            'save_to_gcal': 'on',
        })
        user.refresh_from_db()
        assert user.save_to_gcal is False


@pytest.mark.django_db
class TestRevokeGoogle:
    @patch('accounts.utils.revoke_google_token')
    def test_revokes_and_disables(self, mock_revoke, auth_client, user):
        resp = auth_client.post(reverse('accounts:revoke_google'))
        assert resp.json()['ok']
        user.refresh_from_db()
        assert user.save_to_gcal is False
        mock_revoke.assert_called_once()

    def test_requires_post(self, auth_client):
        resp = auth_client.get(reverse('accounts:revoke_google'))
        assert resp.status_code == 405
