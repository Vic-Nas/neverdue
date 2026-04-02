import pytest
from datetime import datetime, timezone as dt_tz, date
from dashboard.ical import build_ics, _parse_rrule, _ensure_utc
from dashboard.models import Event, Category


@pytest.mark.django_db
class TestBuildIcs:
    def test_produces_ical(self, user):
        e = Event.objects.create(
            user=user, title='Test Event',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
        )
        ics = build_ics([e])
        assert b'Test Event' in ics
        assert b'VCALENDAR' in ics

    def test_with_recurrence(self, user):
        e = Event.objects.create(
            user=user, title='Weekly',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
            recurrence_freq='WEEKLY', recurrence_until=date(2026, 12, 31),
        )
        ics = build_ics([e])
        assert b'RRULE' in ics


class TestParseRrule:
    def test_freq_only(self):
        result = _parse_rrule('FREQ=WEEKLY')
        assert result['FREQ'] == 'WEEKLY'

    def test_with_until(self):
        result = _parse_rrule('FREQ=DAILY;UNTIL=20261231')
        assert 'UNTIL' in result
        assert result['UNTIL'].year == 2026


class TestEnsureUtc:
    def test_naive(self):
        dt = datetime(2026, 1, 1, 12)
        result = _ensure_utc(dt)
        assert result.tzinfo == dt_tz.utc

    def test_none(self):
        result = _ensure_utc(None)
        assert result.tzinfo == dt_tz.utc
