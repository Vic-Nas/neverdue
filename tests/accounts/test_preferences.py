import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestPreferences:
    def test_get_renders(self, auth_client):
        resp = auth_client.get(reverse('accounts:preferences'))
        assert resp.status_code == 200

    def test_post_saves(self, auth_client, user):
        resp = auth_client.post(reverse('accounts:preferences'), {
            'language': 'Français',
            'auto_delete_past_events': 'on',
            'past_event_retention_days': '60',
            'timezone': 'Europe/Paris',
            'priority_color_low': '1',
            'priority_color_medium': '3',
            'priority_color_high': '9',
            'priority_color_urgent': '11',
        })
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.language == 'Français'
        assert user.past_event_retention_days == 60
        assert user.timezone == 'Europe/Paris'
        assert user.priority_color_low == 1

    def test_invalid_tz_falls_back(self, auth_client, user):
        auth_client.post(reverse('accounts:preferences'), {
            'language': 'English',
            'timezone': 'Invalid/Zone',
            'past_event_retention_days': '30',
        })
        user.refresh_from_db()
        assert user.timezone == 'UTC'

    def test_invalid_retention_defaults(self, auth_client, user):
        auth_client.post(reverse('accounts:preferences'), {
            'language': 'English',
            'timezone': 'UTC',
            'past_event_retention_days': 'abc',
        })
        user.refresh_from_db()
        assert user.past_event_retention_days == 30
