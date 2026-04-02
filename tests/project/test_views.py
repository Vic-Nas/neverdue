import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestProjectViews:
    def test_help_page(self, client):
        resp = client.get(reverse('help'))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUrlConf:
    def test_dashboard_urls_resolve(self, auth_client):
        for name in ('dashboard:index', 'dashboard:queue', 'dashboard:categories', 'dashboard:rules', 'dashboard:upload'):
            resp = auth_client.get(reverse(name))
            assert resp.status_code == 200, f'{name} returned {resp.status_code}'
