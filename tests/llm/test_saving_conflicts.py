import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone as dt_tz
from llm.pipeline.saving import _find_conflicts, _append_conflict_concern
from dashboard.models import Event


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
