import pytest
import json
from django.urls import reverse


@pytest.mark.django_db
class TestSetTimezoneAuto:
    def test_sets_utc_user(self, auth_client, user):
        user.timezone = 'UTC'
        user.timezone_auto_detected = False
        user.save()
        resp = auth_client.post(
            reverse('accounts:set_timezone_auto'),
            json.dumps({'timezone': 'America/Toronto'}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.timezone == 'America/Toronto'

    def test_skips_non_utc_user(self, auth_client, user):
        user.timezone = 'Europe/London'
        user.timezone_auto_detected = False
        user.save()
        resp = auth_client.post(
            reverse('accounts:set_timezone_auto'),
            json.dumps({'timezone': 'Asia/Tokyo'}),
            content_type='application/json',
        )
        user.refresh_from_db()
        assert user.timezone == 'Europe/London'

    def test_invalid_tz_rejected(self, auth_client):
        resp = auth_client.post(
            reverse('accounts:set_timezone_auto'),
            json.dumps({'timezone': 'Not/Real'}),
            content_type='application/json',
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestSetTimezoneManual:
    def test_overrides(self, auth_client, user):
        resp = auth_client.post(
            reverse('accounts:set_timezone_manual'),
            json.dumps({'timezone': 'Asia/Tokyo'}),
            content_type='application/json',
        )
        user.refresh_from_db()
        assert user.timezone == 'Asia/Tokyo'
        assert user.timezone_auto_detected is False
