import pytest
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
        assert resp.status_code == 500
