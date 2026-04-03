import pytest
from unittest.mock import patch, MagicMock
from dashboard.gcal.watch import register_gcal_watch
from dashboard.gcal.crud import patch_event_color


@pytest.mark.django_db
class TestRegisterGcalWatch:
    @patch('dashboard.gcal.watch._service')
    def test_success(self, mock_svc, user):
        svc = MagicMock()
        svc.events().watch().execute.return_value = {
            'expiration': '1800000000000', 'resourceId': 'res1',
        }
        mock_svc.return_value = svc
        assert register_gcal_watch(user) is True
        user.refresh_from_db()
        assert user.gcal_channel_id is not None

    @patch('dashboard.gcal.watch._service', side_effect=Exception('no token'))
    def test_token_failure(self, mock_svc, user):
        assert register_gcal_watch(user) is False


@pytest.mark.django_db
class TestPatchEventColor:
    @patch('dashboard.gcal.crud._service')
    def test_success(self, mock_svc, user):
        mock_svc.return_value = MagicMock()
        assert patch_event_color(user, 'gcal123', '5') is True

    def test_empty_id(self, user):
        assert patch_event_color(user, '', '5') is False

    @patch('dashboard.gcal.crud._service', side_effect=Exception('fail'))
    def test_api_error(self, mock_svc, user):
        assert patch_event_color(user, 'gcal123', '5') is False
