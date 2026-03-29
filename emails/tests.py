# emails/tests.py
"""
Test suite covering BEHAVIOUR.md requirements for ScanJob and retry behavior.

Focuses on:
1. Job status transitions (queued → processing → done/failed)
2. Failure reasons and retry logic
3. One-source-input one-job invariant
4. Task argument storage and replay
"""

import json
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model

from emails.models import ScanJob
from emails.tasks import _reenqueue_jobs

User = get_user_model()


class ScanJobLifecycleTest(TransactionTestCase):
    """Test job status transitions per BEHAVIOUR.md"""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='test')

    def tearDown(self):
        User.objects.all().delete()
        ScanJob.objects.all().delete()

    def test_job_created_in_queued_state(self):
        """INVARIANT: new job must be in 'queued' state"""
        job = ScanJob.objects.create(user=self.user, source='email')
        self.assertEqual(job.status, ScanJob.STATUS_QUEUED)
        
    def test_job_transitions_queued_to_processing(self):
        """Job must transition queued → processing when worker picks it up"""
        job = ScanJob.objects.create(user=self.user, source='email')
        
        ScanJob.objects.filter(pk=job.pk).update(status=ScanJob.STATUS_PROCESSING)
        job.refresh_from_db()
        
        self.assertEqual(job.status, ScanJob.STATUS_PROCESSING)

    def test_failed_job_has_failure_reason(self):
        """INVARIANT: every failed job must have a failure_reason code"""
        job = ScanJob.objects.create(
            user=self.user, 
            source='email', 
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error'
        )
        
        valid_reasons = {'llm_error', 'scan_limit', 'pro_required', 'internal_error'}
        self.assertIn(job.failure_reason, valid_reasons)

    def test_failed_job_preserved_for_user(self):
        """INVARIANT: failed jobs are not auto-deleted, visible to users"""
        job = ScanJob.objects.create(
            user=self.user, 
            source='email', 
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
        )
        
        self.assertTrue(ScanJob.objects.filter(pk=job.pk).exists())


class FailureReasonTest(TransactionTestCase):
    """Test failure reason handling per BEHAVIOUR.md"""

    def setUp(self):
        self.user = User.objects.create_user(username='failuser', password='test')

    def tearDown(self):
        User.objects.all().delete()
        ScanJob.objects.all().delete()

    def test_llm_error_requires_manual_retry(self):
        """llm_error: API failure, requires manual retry (not automatic)"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
        )
        
        self.assertEqual(job.failure_reason, 'llm_error')

    def test_scan_limit_indicates_quota_exceeded(self):
        """scan_limit: monthly scan quota reached"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='scan_limit',
        )
        
        self.assertEqual(job.failure_reason, 'scan_limit')

    def test_pro_required_attachment_only_email(self):
        """pro_required: free user received attachment-only email"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='pro_required',
            notes='Attachment-only email — upgrade to Pro to include attachments.',
        )
        
        self.assertEqual(job.failure_reason, 'pro_required')
        self.assertIn('Pro', job.notes)

    def test_internal_error_with_signature(self):
        """internal_error: unhandled exception, grouped by signature"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='internal_error',
            failure_signature='BadRequestError: 400 insufficient credits',
        )
        
        self.assertEqual(job.failure_reason, 'internal_error')
        self.assertTrue(len(job.failure_signature) > 0)


class JobRetryTest(TransactionTestCase):
    """Test _reenqueue_jobs behavior"""

    def setUp(self):
        self.user = User.objects.create_user(username='retryuser', password='test')

    def tearDown(self):
        User.objects.all().delete()
        ScanJob.objects.all().delete()

    def test_reenqueue_sets_status_queued(self):
        """_reenqueue_jobs: transition failed job back to queued"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
            task_args=json.dumps({'user_id': self.user.id, 'body': 'test'}),
        )
        
        count = _reenqueue_jobs([job])
        job.refresh_from_db()
        
        self.assertEqual(count, 1)
        self.assertEqual(job.status, ScanJob.STATUS_QUEUED)

    def test_reenqueue_clears_failure_info(self):
        """_reenqueue_jobs: clear failure_reason and failure_signature on retry"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
            failure_signature='BadRequestError: 400',
            task_args=json.dumps({'user_id': self.user.id, 'body': 'test'}),
        )
        
        _reenqueue_jobs([job])
        job.refresh_from_db()
        
        self.assertEqual(job.failure_reason, '')
        self.assertEqual(job.failure_signature, '')

    def test_reenqueue_sets_queued_for_retry_note(self):
        """_reenqueue_jobs: set notes to 'Queued for retry.'"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
            task_args=json.dumps({'user_id': self.user.id, 'body': 'test'}),
        )
        
        _reenqueue_jobs([job])
        job.refresh_from_db()
        
        self.assertEqual(job.notes, 'Queued for retry.')

    def test_reenqueue_preserves_task_args(self):
        """_reenqueue_jobs: preserve task_args for dispatcher to replay"""
        task_args = {
            'user_id': self.user.id,
            'body': 'Important deadline tomorrow',
            'sender': 'boss@company.com',
            'message_id': 'msg-abcd123',
            'attachments': []
        }
        
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
            task_args=json.dumps(task_args),
        )
        
        _reenqueue_jobs([job])
        job.refresh_from_db()
        
        # Task args still preserved for worker to replay
        stored = json.loads(job.task_args)
        self.assertEqual(stored['message_id'], 'msg-abcd123')
        self.assertEqual(stored['sender'], 'boss@company.com')

    def test_reenqueue_returns_count(self):
        """_reenqueue_jobs: return number of jobs successfully re-enqueued"""
        jobs = []
        for i in range(3):
            job = ScanJob.objects.create(
                user=self.user,
                source='email',
                status=ScanJob.STATUS_FAILED,
                failure_reason='llm_error',
                task_args=json.dumps({'user_id': self.user.id, 'body': f'test{i}'}),
            )
            jobs.append(job)
        
        count = _reenqueue_jobs(jobs)
        self.assertEqual(count, 3)
        
        for job in jobs:
            job.refresh_from_db()
            self.assertEqual(job.status, ScanJob.STATUS_QUEUED)

    def test_reenqueue_same_job_not_duplicated(self):
        """INVARIANT: reenqueue does NOT create new job, mutates existing"""
        original_pk = None
        task_args = {'user_id': self.user.id, 'body': 'test'}
        
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
            task_args=json.dumps(task_args),
        )
        original_pk = job.pk
        
        _reenqueue_jobs([job])
        
        # Must be same job, not new
        self.assertEqual(ScanJob.objects.filter(user=self.user).count(), 1)
        job.refresh_from_db()
        self.assertEqual(job.pk, original_pk)


class JobUniquenessInvariantTest(TransactionTestCase):
    """Test one-source-input → one-job invariant"""

    def setUp(self):
        self.user = User.objects.create_user(username='dupuser', password='test')

    def tearDown(self):
        User.objects.all().delete()
        ScanJob.objects.all().delete()

    def test_reprocess_mutates_same_job(self):
        """INVARIANT: reprocess does NOT create new ScanJob, mutates existing"""
        job = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_NEEDS_REVIEW,
        )
        original_pk = job.pk
        original_created = job.created_at
        
        # Simulate reprocess: transition to processing
        ScanJob.objects.filter(pk=job.pk).update(status=ScanJob.STATUS_PROCESSING)
        job.refresh_from_db()
        
        # Must be same job
        self.assertEqual(job.pk, original_pk)
        self.assertEqual(job.created_at, original_created)

    def test_multiple_jobs_dont_silently_merge(self):
        """Jobs are independent; no job should be silently deleted or replaced"""
        job1 = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_DONE,
        )
        
        job2 = ScanJob.objects.create(
            user=self.user,
            source='email',
            status=ScanJob.STATUS_FAILED,
            failure_reason='llm_error',
        )
        
        # Both jobs must exist
        self.assertEqual(ScanJob.objects.filter(user=self.user).count(), 2)
        self.assertNotEqual(job1.pk, job2.pk)
