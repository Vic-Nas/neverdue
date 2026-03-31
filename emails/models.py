# emails/models.py
from django.db import models
from django.conf import settings


class ScanJob(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_NEEDS_REVIEW = 'needs_review'
    STATUS_DONE = 'done'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_NEEDS_REVIEW, 'Needs review'),
        (STATUS_DONE, 'Done'),
        (STATUS_FAILED, 'Failed'),
    ]

    # Source constants — 'reprocess' is intentionally absent.
    # Reprocess reuses the original job; a job with source='reprocess'
    # indicates a contract violation.
    SOURCE_EMAIL = 'email'
    SOURCE_UPLOAD = 'upload'

    SOURCE_CHOICES = [
        (SOURCE_EMAIL, 'Email'),
        (SOURCE_UPLOAD, 'Upload'),
    ]

    # Failure reason codes — set on every failed job so admin can filter
    # and bulk-retry by root cause.
    REASON_LLM_ERROR = 'llm_error'
    REASON_SCAN_LIMIT = 'scan_limit'
    REASON_PRO_REQUIRED = 'pro_required'
    REASON_INTERNAL_ERROR = 'internal_error'
    # Not a failure — job completes as done. Used only in notes for UI display.
    REASON_DISCARDED_BY_RULE = 'discarded_by_rule'

    FAILURE_REASON_CHOICES = [
        (REASON_LLM_ERROR, 'LLM error'),
        (REASON_SCAN_LIMIT, 'Scan limit reached'),
        (REASON_PRO_REQUIRED, 'Pro plan required'),
        (REASON_INTERNAL_ERROR, 'Internal error'),
        (REASON_DISCARDED_BY_RULE, 'Discarded by rule'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='scan_jobs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_EMAIL)
    from_address = models.CharField(max_length=255, blank=True, default='')
    notes = models.CharField(max_length=255, blank=True, default='')

    # Serialized task arguments for replay on retry.
    # JSON-encoded dict with task-specific kwargs (e.g. body, attachments for email tasks).
    # Allows failed jobs to be re-enqueued with original args after plan/quota changes.
    task_args = models.TextField(blank=True, default='{}')

    # Failure classification — both blank on non-failed jobs.
    # failure_reason: controlled code for admin filtering and bulk retry logic.
    # failure_signature: short string identifying the exception type + message
    #   (e.g. "AnthropicError: 529 overloaded") so internal_error jobs can be
    #   grouped by root cause in the admin.
    failure_reason = models.CharField(
        max_length=30, choices=FAILURE_REASON_CHOICES, blank=True, default='',
    )
    failure_signature = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'ScanJob({self.pk}) user={self.user_id} source={self.source} status={self.status}'

    @property
    def duration_seconds(self):
        """Wall-clock seconds from creation to last update. Meaningful once done/failed."""
        return (self.updated_at - self.created_at).total_seconds()

    @property
    def pending_events(self):
        return self.events.filter(status='pending')

    @property
    def active_events(self):
        return self.events.filter(status='active')


class JobAttemptLog(models.Model):
    """
    Immutable log of every job execution attempt.
    Used for metrics that must survive job retry/success transitions.

    Each attempt (initial + retries) creates a row.
    Metrics queries this table instead of querying ScanJob.status,
    so failure history isn't lost when jobs are retried and succeed.
    """
    STATUS_DONE = 'done'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_DONE, 'Done'),
        (STATUS_FAILED, 'Failed'),
    ]

    job = models.ForeignKey(ScanJob, on_delete=models.CASCADE, related_name='attempt_logs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    failure_reason = models.CharField(
        max_length=30,
        choices=ScanJob.FAILURE_REASON_CHOICES,
        blank=True,
        default='',
        help_text='Only populated if status=failed'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status', 'failure_reason']),
        ]

    def __str__(self):
        return f'JobAttemptLog(job={self.job_id} status={self.status} reason={self.failure_reason})'