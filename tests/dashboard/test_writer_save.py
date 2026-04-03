import pytest
from datetime import datetime, timezone as dt_tz
from unittest.mock import patch, MagicMock
from dashboard.writer import write_event_to_calendar, GCalUnavailableError
from dashboard.models import Event


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
        })
        assert result is not None
        assert result.google_event_id == 'gcal_abc'

    @patch('dashboard.gcal.client._service', side_effect=Exception('no token'))
    def test_gcal_failure_raises_when_sync_on(self, mock_svc, user):
        """save_to_gcal=True (default) + GCal fails → GCalUnavailableError."""
        with pytest.raises(GCalUnavailableError):
            write_event_to_calendar(user, {
                'title': 'Fail',
                'start': '2026-08-01T09:00:00+00:00',
                'end': '2026-08-01T10:00:00+00:00',
            })

    def test_saves_locally_when_sync_off(self, user):
        """save_to_gcal=False → saves to DB without touching GCal."""
        user.save_to_gcal = False
        user.save(update_fields=['save_to_gcal'])
        result = write_event_to_calendar(user, {
            'title': 'Local Only',
            'start': '2026-08-01T09:00:00+00:00',
            'end': '2026-08-01T10:00:00+00:00',
        })
        assert result is not None
        assert result.google_event_id is None
