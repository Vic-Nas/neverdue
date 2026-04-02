import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone as dt_tz
from dashboard.tasks import patch_category_colors
from dashboard.models import Category, Event


@pytest.mark.django_db
class TestPatchCategoryColors:
    @patch('dashboard.gcal.patch_event_color', return_value=True)
    def test_patches_active_events(self, mock_patch, user):
        cat = Category.objects.create(user=user, name='Exams', priority=4, gcal_color_id='11')
        Event.objects.create(
            user=user, category=cat, title='Final',
            start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 15, 10, tzinfo=dt_tz.utc),
            google_event_id='gcal_1', status='active',
        )
        patch_category_colors(user.pk, cat.pk)
        mock_patch.assert_called_once_with(user, 'gcal_1', '11')

    @patch('dashboard.gcal.patch_event_color')
    def test_skips_no_color(self, mock_patch, user):
        cat = Category.objects.create(user=user, name='Misc', priority=1)
        # Category.save() auto-assigns gcal_color_id — force it blank after save
        Category.objects.filter(pk=cat.pk).update(gcal_color_id='')
        Event.objects.create(
            user=user, category=cat, title='X',
            start=datetime(2026, 6, 15, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 15, 10, tzinfo=dt_tz.utc),
            google_event_id='gcal_2', status='active',
        )
        patch_category_colors(user.pk, cat.pk)
        mock_patch.assert_not_called()

    def test_missing_category(self, user):
        # Should not raise
        patch_category_colors(user.pk, 99999)

    def test_missing_user(self, user):
        cat = Category.objects.create(user=user, name='X', priority=1)
        patch_category_colors(99999, cat.pk)
