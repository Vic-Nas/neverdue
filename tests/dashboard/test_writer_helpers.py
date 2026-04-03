import pytest
from datetime import date
from unittest.mock import MagicMock
from dashboard.writer import _build_rrule, _priority_color_id, _resolve_color_id


class TestBuildRrule:
    def test_with_until(self):
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
