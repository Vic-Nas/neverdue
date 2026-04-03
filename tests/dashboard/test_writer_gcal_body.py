import pytest
from unittest.mock import MagicMock
from dashboard.writer import _build_gcal_body, _build_gcal_body_from_dict


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
