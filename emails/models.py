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
    REASON_DISCARDED_BY_RULE = 'discarded_by_rule'
    REASON_GCAL_DISCONNECTED = 'gcal_disconnected'

    FAILURE_REASON_CHOICES = [
        (REASON_LLM_ERROR, 'LLM error'),
        (REASON_SCAN_LIMIT, 'Scan limit reached'),
        (REASON_PRO_REQUIRED, 'Pro plan required'),
        (REASON_INTERNAL_ERROR, 'Internal error'),
        (REASON_DISCARDED_BY_RULE, 'Discarded by rule'),
        (REASON_GCAL_DISCONNECTED, 'Google Calendar disconnected'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='scan_jobs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_EMAIL)
    from_address = models.CharField(max_length=255, blank=True, default='')
    notes = models.CharField(max_length=255, blank=True, default='')

    # Failure classification — blank on non-failed jobs.
    # failure_reason: controlled code for admin filtering and bulk retry logic.
    failure_reason = models.CharField(
        max_length=30, choices=FAILURE_REASON_CHOICES, blank=True, default='',
    )

    # Typed retry fields — store only what each source type needs to re-dispatch.
    # Email jobs: message_id is sufficient to re-fetch from Resend + dedup guard.
    # Upload jobs: file_b64/media_type/context/filename to rerun process_uploaded_file,
    #              or upload_text to rerun process_text_as_upload.
    # Fields are blank/null when not applicable to the source type.
    message_id = models.CharField(max_length=255, blank=True, default='')  # email source
    email_id = models.CharField(max_length=255, blank=True, default='')    # email source
    file_b64 = models.TextField(blank=True, default='')                    # upload source (file)
    media_type = models.CharField(max_length=100, blank=True, default='')  # upload source (file)
    upload_context = models.TextField(blank=True, default='')              # upload source (file)
    filename = models.CharField(max_length=255, blank=True, default='')    # upload source (file)
    upload_text = models.TextField(blank=True, default='')                 # upload source (text)

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