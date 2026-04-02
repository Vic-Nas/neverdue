import pytest
from unittest.mock import patch, MagicMock
from dashboard.gcal.signals import event_pre_delete


@pytest.mark.django_db
class TestEventPreDeleteSignal:
    @patch('dashboard.gcal.signals.delete_from_gcal')
    def test_calls_gcal_delete(self, mock_del, user):
        event = MagicMock(
            status='active', google_event_id='gcal_1', user=user,
            _skip_gcal_delete=False,
        )
        # hasattr check
        delattr(event, '_skip_gcal_delete')
        event._skip_gcal_delete = False
        event_pre_delete(sender=None, instance=event)
        mock_del.assert_called_once_with(user, 'gcal_1')

    @patch('dashboard.gcal.signals.delete_from_gcal')
    def test_skips_pending(self, mock_del, user):
        event = MagicMock(status='pending', google_event_id='gcal_1')
        event._skip_gcal_delete = False
        event_pre_delete(sender=None, instance=event)
        mock_del.assert_not_called()

    @patch('dashboard.gcal.signals.delete_from_gcal')
    def test_skips_flagged(self, mock_del, user):
        event = MagicMock(status='active', google_event_id='gcal_1')
        event._skip_gcal_delete = True
        event_pre_delete(sender=None, instance=event)
        mock_del.assert_not_called()
