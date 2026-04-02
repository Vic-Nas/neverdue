import pytest
from unittest.mock import patch, MagicMock
from llm.pipeline.outcome import ProcessingOutcome
from llm.pipeline.saving import (
    _check_and_increment_scans, _fire_usage,
    _get_or_create_uncategorized, _find_conflicts,
    _append_conflict_concern, _save_events,
)
from dashboard.models import Event, Category
from datetime import datetime, timezone as dt_tz


class TestProcessingOutcome:
    def test_defaults(self):
        o = ProcessingOutcome()
        assert o.status == 'done'
        assert o.created == []
        assert o.failure_reason == ''


@pytest.mark.django_db
class TestCheckAndIncrementScans:
    def test_free_user_increments(self, user):
        from django.utils import timezone as tz
        user.monthly_scans = 0
        user.scan_reset_date = tz.now().date()
        user.save()
        assert _check_and_increment_scans(user) is True

    def test_free_user_at_limit(self, user):
        from django.utils import timezone as tz
        user.monthly_scans = 30
        user.scan_reset_date = tz.now().date()
        user.save()
        assert _check_and_increment_scans(user) is False

    def test_pro_user_always_passes(self, pro_user):
        from django.utils import timezone as tz
        pro_user.monthly_scans = 999
        pro_user.scan_reset_date = tz.now().date()
        pro_user.save()
        assert _check_and_increment_scans(pro_user) is True


@pytest.mark.django_db
class TestGetOrCreateUncategorized:
    def test_creates(self, user):
        cat = _get_or_create_uncategorized(user)
        assert cat.name == 'Uncategorized'

    def test_idempotent(self, user):
        c1 = _get_or_create_uncategorized(user)
        c2 = _get_or_create_uncategorized(user)
        assert c1.pk == c2.pk


@pytest.mark.django_db
class TestFindConflicts:
    def test_by_email_id(self, user):
        Event.objects.create(
            user=user, title='Existing',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
            source_email_id='eid1',
        )
        conflicts = _find_conflicts(user, {'source_email_id': 'eid1', 'title': 'X'})
        assert len(conflicts) == 1

    def test_no_conflicts(self, user):
        conflicts = _find_conflicts(user, {'title': 'X', 'start': '2099-01-01T09:00:00+00:00'})
        assert len(conflicts) == 0


@pytest.mark.django_db
class TestAppendConflictConcern:
    def test_sets_pending(self):
        e = MagicMock(title='Dup', start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc), pk=1)
        data = {'status': 'active', 'concern': ''}
        result = _append_conflict_concern(data, [e])
        assert result['status'] == 'pending'
        assert 'Conflicts' in result['concern']
