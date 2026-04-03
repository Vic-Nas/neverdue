import pytest
from emails.models import ScanJob
from datetime import timedelta
from django.utils import timezone


@pytest.mark.django_db
class TestScanJob:
    def test_str(self, user):
        job = ScanJob.objects.create(user=user, source='email')
        assert 'ScanJob' in str(job)

    def test_duration_seconds(self, user):
        job = ScanJob.objects.create(user=user, source='email')
        ScanJob.objects.filter(pk=job.pk).update(
            updated_at=job.created_at + timedelta(seconds=30),
        )
        job.refresh_from_db()
        assert abs(job.duration_seconds - 30) < 1
