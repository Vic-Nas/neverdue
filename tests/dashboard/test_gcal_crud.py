import pytest
from unittest.mock import patch, MagicMock
from dashboard.gcal.crud import delete_from_gcal
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
