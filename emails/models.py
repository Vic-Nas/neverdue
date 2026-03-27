# emails/models.py
from django.db import models
from django.conf import settings


class ScanJob(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_DONE = 'done'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_DONE, 'Done'),
        (STATUS_FAILED, 'Failed'),
    ]

    SOURCE_EMAIL = 'email'
    SOURCE_UPLOAD = 'upload'
    SOURCE_REPROCESS = 'reprocess'

    SOURCE_CHOICES = [
        (SOURCE_EMAIL, 'Email'),
        (SOURCE_UPLOAD, 'Upload'),
        (SOURCE_REPROCESS, 'Reprocess'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='scan_jobs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_EMAIL)
    # Human-readable hint: sender address, filename, prompt snippet, etc.
    summary = models.CharField(max_length=255, blank=True, default='')
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
