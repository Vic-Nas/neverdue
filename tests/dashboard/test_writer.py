import pytest
from datetime import datetime, timezone as dt_tz
from unittest.mock import patch, MagicMock
from dashboard.writer import (
    _build_rrule, _priority_color_id, _resolve_color_id,
    _build_gcal_body, _build_gcal_body_from_dict, write_event_to_calendar,
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

    @patch('dashboard.gcal.client._service')
    def test_active_saved_with_gcal(self, mock_svc, user):
        svc = MagicMock()
        svc.events().insert().execute.return_value = {
            'id': 'gcal_abc', 'htmlLink': 'https://cal.google.com/event/abc',
        }
        mock_svc.return_value = svc
        result = write_event_to_calendar(user, {
            'title': 'Exam',
            'start': '2026-08-01T09:00:00+00:00',
            'end': '2026-08-01T10:00:00+00:00',
            'status': 'active', 'description': 'Final exam',
        })
        assert result is not None
        assert result.status == 'active'
        assert result.google_event_id == 'gcal_abc'
        assert result.gcal_link == 'https://cal.google.com/event/abc'

    @patch('dashboard.gcal.client._service', side_effect=Exception('no token'))
    def test_active_no_token_returns_none(self, mock_svc, user):
        result = write_event_to_calendar(user, {
            'title': 'X',
            'start': '2026-08-01T09:00:00+00:00',
            'end': '2026-08-01T10:00:00+00:00',
        })
        assert result is None


@pytest.mark.django_db
class TestBuildGcalBody:
    def test_basic_event(self, user):
        cat = MagicMock(gcal_color_id='3', priority=2, reminders=None)
        event = MagicMock(
            title='Exam', description='Final',
            start=MagicMock(isoformat=lambda: '2026-06-01T09:00:00+00:00'),
            end=MagicMock(isoformat=lambda: '2026-06-01T10:00:00+00:00'),
            recurrence_freq=None, user=user, category=cat, color='',
        )
        body = _build_gcal_body(event)
        assert body['summary'] == 'Exam'
        assert body['description'] == 'Final'
        assert 'recurrence' not in body

    def test_with_recurrence(self, user):
        cat = MagicMock(gcal_color_id='', priority=2, reminders=None)
        event = MagicMock(
            title='Class', description='',
            start=MagicMock(isoformat=lambda: '2026-06-01T09:00:00+00:00'),
            end=MagicMock(isoformat=lambda: '2026-06-01T10:00:00+00:00'),
            recurrence_freq='WEEKLY', recurrence_until=None,
            user=user, category=cat, color='',
        )
        body = _build_gcal_body(event)
        assert 'recurrence' in body
        assert 'FREQ=WEEKLY' in body['recurrence'][0]


@pytest.mark.django_db
class TestBuildGcalBodyFromDict:
    def test_basic(self, user):
        cat = MagicMock(gcal_color_id='5', priority=3, reminders=None)
        body = _build_gcal_body_from_dict(user, {
            'title': 'Lab', 'start': '2026-06-01T14:00:00',
            'end': '2026-06-01T16:00:00', 'description': 'Physics lab',
        }, cat)
        assert body['summary'] == 'Lab'
        assert body['colorId'] == '5'

    def test_with_reminders(self, user):
        cat = MagicMock(gcal_color_id='', priority=2, reminders=[{'minutes': 30}])
        body = _build_gcal_body_from_dict(user, {
            'title': 'Meet', 'start': '2026-06-01T10:00:00',
            'end': '2026-06-01T11:00:00',
        }, cat)
        assert body['reminders']['overrides'] == [{'method': 'popup', 'minutes': 30}]
