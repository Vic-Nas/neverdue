import pytest
from datetime import datetime, timezone as dt_tz
from unittest.mock import patch, MagicMock
from dashboard.writer import (
    _build_rrule, _priority_color_id, _resolve_color_id,
    _build_gcal_body, write_event_to_calendar,
)
from dashboard.models import Event, Category


class TestBuildRrule:
    def test_with_until(self):
        from datetime import date
        r = _build_rrule('WEEKLY', date(2026, 12, 31))
        assert 'FREQ=WEEKLY' in r
        assert '20261231' in r

    def test_no_until(self):
        r = _build_rrule('DAILY', None)
        assert r == 'RRULE:FREQ=DAILY'


@pytest.mark.django_db
class TestPriorityColorId:
    def test_default(self, user):
        assert _priority_color_id(user, 1) == str(user.priority_color_low)

    def test_urgent(self, user):
        assert _priority_color_id(user, 4) == str(user.priority_color_urgent)


@pytest.mark.django_db
class TestResolveColorId:
    def test_event_color_wins(self, user):
        assert _resolve_color_id(user, None, '7') == '7'

    def test_category_color(self, user):
        cat = MagicMock(gcal_color_id='3', priority=2)
        assert _resolve_color_id(user, cat) == '3'


@pytest.mark.django_db
class TestWriteEventToCalendar:
    def test_duplicate_skipped(self, user):
        Event.objects.create(
            user=user, title='Dup',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
        )
        result = write_event_to_calendar(user, {
            'title': 'Dup',
            'start': '2026-06-01T09:00:00+00:00',
            'end': '2026-06-01T10:00:00+00:00',
        })
        assert result is None

    def test_pending_saved(self, user):
        result = write_event_to_calendar(user, {
            'title': 'Maybe',
            'start': '2026-07-01T09:00:00+00:00',
            'end': '2026-07-01T10:00:00+00:00',
            'status': 'pending', 'concern': 'Unclear date',
        })
        assert result is not None
        assert result.status == 'pending'
