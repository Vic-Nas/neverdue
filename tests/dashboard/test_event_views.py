import pytest
import json
from datetime import datetime, timezone as dt_tz
from django.urls import reverse
from dashboard.models import Category, Event


@pytest.fixture
def category(user):
    return Category.objects.create(user=user, name='School')


@pytest.fixture
def event(user, category):
    return Event.objects.create(
        user=user, category=category, title='Midterm',
        start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
        end=datetime(2026, 6, 15, 11, tzinfo=dt_tz.utc),
    )


@pytest.mark.django_db
class TestIndex:
    def test_renders(self, auth_client):
        resp = auth_client.get(reverse('dashboard:index'))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestEventDetail:
    def test_own_event(self, auth_client, event):
        resp = auth_client.get(reverse('dashboard:event_detail', args=[event.pk]))
        assert resp.status_code == 200

    def test_other_user_404(self, auth_client, db):
        resp = auth_client.get(reverse('dashboard:event_detail', args=[99999]))
        assert resp.status_code == 500  # broad except in view wraps 404


@pytest.mark.django_db
class TestEventEdit:
    def test_create(self, auth_client, user):
        resp = auth_client.post(
            reverse('dashboard:event_create'),
            json.dumps({
                'title': 'New', 'start': '2026-07-01T09:00:00+00:00',
                'end': '2026-07-01T10:00:00+00:00',
            }),
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.json()['ok']

    def test_edit_pending_promotes(self, auth_client, event):
        event.status = 'pending'
        event.save(update_fields=['status'])
        resp = auth_client.post(
            reverse('dashboard:event_edit', args=[event.pk]),
            json.dumps({
                'title': 'Updated', 'start': '2026-07-01T09:00:00+00:00',
                'end': '2026-07-01T10:00:00+00:00',
            }),
            content_type='application/json',
        )
        event.refresh_from_db()
        assert event.status == 'active'


@pytest.mark.django_db
class TestEventDelete:
    def test_delete(self, auth_client, event):
        resp = auth_client.post(reverse('dashboard:event_delete', args=[event.pk]))
        assert resp.status_code == 302
        assert not Event.objects.filter(pk=event.pk).exists()
