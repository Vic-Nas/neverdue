import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestLogin:
    def test_authenticated_redirects(self, auth_client):
        resp = auth_client.get(reverse('accounts:login'))
        assert resp.status_code == 302
        assert '/dashboard/' in resp.url

    def test_anonymous_renders(self, client):
        resp = client.get(reverse('accounts:login'))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestLogout:
    def test_logs_out(self, auth_client):
        resp = auth_client.get(reverse('accounts:logout'))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUsernamePick:
    def test_anonymous_redirects(self, client):
        resp = client.get(reverse('accounts:username_pick'))
        assert resp.status_code == 302

    def test_existing_username_redirects(self, auth_client, user):
        user.username = 'custom'
        user.save()
        resp = auth_client.get(reverse('accounts:username_pick'))
        assert resp.status_code == 302

    def test_empty_username_rejected(self, auth_client, user):
        user.username = user.email.split('@')[0]
        user.save()
        resp = auth_client.post(reverse('accounts:username_pick'), {'username': ''})
        assert resp.status_code == 200

    def test_reserved_username_rejected(self, auth_client, user):
        user.username = user.email.split('@')[0]
        user.save()
        resp = auth_client.post(reverse('accounts:username_pick'), {'username': 'admin'})
        assert resp.status_code == 200
