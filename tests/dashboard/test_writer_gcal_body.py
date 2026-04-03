import pytest
from unittest.mock import MagicMock
from dashboard.writer import _build_gcal_body_from_dict


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
