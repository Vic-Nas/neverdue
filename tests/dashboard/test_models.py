import pytest
from datetime import datetime, timezone as dt_tz, date
from django.core.exceptions import ValidationError
from dashboard.models import Event, Category


@pytest.mark.django_db
class TestCategory:
    def test_str(self, user):
        cat = Category.objects.create(user=user, name='Work')
        assert str(cat) == 'Work'

    def test_unique_per_user(self, user):
        Category.objects.create(user=user, name='Work')
        with pytest.raises(Exception):
            Category.objects.create(user=user, name='Work')

    def test_auto_gcal_color(self, user):
        cat = Category.objects.create(user=user, name='Test', priority=3)
        assert cat.gcal_color_id != ''


@pytest.mark.django_db
class TestEvent:
    def _make(self, user, **kw):
        defaults = dict(
            user=user, title='Test',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
        )
        defaults.update(kw)
        return Event(**defaults)

    def test_end_before_start_raises(self, user):
        e = self._make(user, end=datetime(2026, 5, 31, 8, tzinfo=dt_tz.utc))
        with pytest.raises(ValidationError, match='End time'):
            e.full_clean()

    def test_recurrence_too_long(self, user):
        e = self._make(
            user, recurrence_freq='DAILY',
            end=datetime(2026, 6, 3, 9, tzinfo=dt_tz.utc),
        )
        with pytest.raises(ValidationError, match='recurrence interval'):
            e.full_clean()

    def test_recurrence_until_before_start(self, user):
        e = self._make(
            user, recurrence_freq='WEEKLY',
            recurrence_until=date(2026, 5, 1),
        )
        with pytest.raises(ValidationError, match='Recurrence end'):
            e.full_clean()

    def test_rrule_property(self, user):
        e = self._make(user, recurrence_freq='WEEKLY', recurrence_until=date(2026, 12, 31))
        e.save()
        assert 'FREQ=WEEKLY' in e.rrule
        assert '20261231' in e.rrule

    def test_serialize_as_text(self, user):
        e = self._make(user)
        e.save()
        txt = e.serialize_as_text()
        assert 'Title: Test' in txt

    def test_str(self, user):
        e = self._make(user)
        e.save()
        assert 'Test' in str(e)
