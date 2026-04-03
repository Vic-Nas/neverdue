import pytest
from unittest.mock import patch, MagicMock
from dashboard.gcal.crud import delete_from_gcal, push_event_to_gcal, update_event_in_gcal
from googleapiclient.errors import HttpError


@pytest.mark.django_db
class TestDeleteFromGcal:
    @patch('dashboard.gcal.crud._service')
    def test_success(self, mock_svc, user):
        mock_svc.return_value = MagicMock()
        assert delete_from_gcal(user, 'gcal123') is True

    @patch('dashboard.gcal.crud._service')
    def test_404_returns_true(self, mock_svc, user):
        svc = MagicMock()
        resp = MagicMock(status=404)
        svc.events().delete().execute.side_effect = HttpError(resp, b'')
        mock_svc.return_value = svc
        assert delete_from_gcal(user, 'gcal123') is True

    def test_empty_id(self, user):
        assert delete_from_gcal(user, '') is False


@pytest.mark.django_db
class TestPushEventToGcal:
    @patch('dashboard.gcal.crud._service')
    def test_success(self, mock_svc, user):
        svc = MagicMock()
        svc.events().insert().execute.return_value = {
            'htmlLink': 'https://cal.google.com/event/1', 'id': 'gcal_1',
        }
        mock_svc.return_value = svc
        event = MagicMock(
            title='T', description='',
            start=MagicMock(isoformat=lambda: '2026-06-01T09:00:00Z'),
            end=MagicMock(isoformat=lambda: '2026-06-01T10:00:00Z'),
            recurrence_freq=None, user=user, category=None, color='',
        )
        assert push_event_to_gcal(user, event) is not None


@pytest.mark.django_db
class TestUpdateEventInGcal:
    @patch('dashboard.gcal.crud._service')
    def test_success(self, mock_svc, user):
        mock_svc.return_value = MagicMock()
        event = MagicMock(
            google_event_id='gcal123', title='T', description='',
            start=MagicMock(isoformat=lambda: '2026-06-01T09:00:00Z'),
            end=MagicMock(isoformat=lambda: '2026-06-01T10:00:00Z'),
            recurrence_freq=None, user=user, category=None, color='',
        )
        assert update_event_in_gcal(user, event) is True

    def test_no_gcal_id(self, user):
        assert update_event_in_gcal(user, MagicMock(google_event_id='')) is False

    @patch('dashboard.gcal.crud._service', side_effect=Exception('fail'))
    def test_api_error(self, mock_svc, user):
        assert update_event_in_gcal(user, MagicMock(google_event_id='gcal123')) is False
