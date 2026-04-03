import pytest
from emails.models import ScanJob
from emails.tasks.processing import process_inbound_email


@pytest.mark.django_db
class TestProcessInboundEdge:
    def test_blocked_sender(self, user):
        from dashboard.models import Rule
        Rule.objects.create(user=user, rule_type='sender', pattern='@spam.com', action='block')
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'evil@spam.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE
        assert 'blocked' in job.notes.lower()

    def test_duplicate_skipped(self, user):
        from dashboard.models import Event
        from datetime import datetime, timezone as dt_tz
        Event.objects.create(
            user=user, title='X', source_email_id='mid',
            start=datetime(2026, 6, 1, 9, tzinfo=dt_tz.utc),
            end=datetime(2026, 6, 1, 10, tzinfo=dt_tz.utc),
        )
        job = ScanJob.objects.create(user=user, source='email', status='queued')
        process_inbound_email(job.pk, user.pk, 'eid', 'a@b.com', 'mid')
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_DONE
        assert 'already processed' in job.notes.lower()
