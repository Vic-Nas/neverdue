import pytest
from unittest.mock import patch
from datetime import datetime, timezone as dt_tz, timedelta, date
from django.utils import timezone
from emails.models import ScanJob
from emails.tasks.scheduled import cleanup_events
from dashboard.models import Event


@pytest.mark.django_db
class TestCleanupEvents:
    def test_deletes_expired_pending(self, user):
        Event.objects.create(
            user=user, title='Old',
            start=datetime(2026, 1, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 1, 1, 10, tzinfo=dt_tz.utc),
            status='pending', pending_expires_at=date(2020, 1, 1),
        )
        cleanup_events(0)
        assert Event.objects.filter(title='Old').count() == 0

    @patch('dashboard.gcal.crud.delete_from_gcal')
    def test_deletes_past_active(self, mock_gcal, user):
        user.auto_delete_past_events = True
        user.past_event_retention_days = 1
        user.save()
        Event.objects.create(
            user=user, title='Past',
            start=datetime(2024, 1, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2024, 1, 1, 10, tzinfo=dt_tz.utc),
        )
        cleanup_events(0)
        assert Event.objects.filter(title='Past').count() == 0

    def test_deletes_done_jobs(self, user):
        job = ScanJob.objects.create(user=user, source='email', status='done')
        ScanJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(days=2),
        )
        cleanup_events(0)
        assert not ScanJob.objects.filter(pk=job.pk).exists()

    def test_deletes_old_needs_review_jobs(self, user):
        job = ScanJob.objects.create(user=user, source='upload', status='needs_review')
        ScanJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(days=31),
        )
        cleanup_events(0)
        assert not ScanJob.objects.filter(pk=job.pk).exists()
