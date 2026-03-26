# accounts/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    google_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    google_calendar_token = models.TextField(null=True, blank=True)
    google_refresh_token = models.TextField(null=True, blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    monthly_scans = models.IntegerField(default=0)
    scan_reset_date = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=50, default='English')

    @property
    def is_pro(self):
        return hasattr(self, 'subscription') and self.subscription.is_pro

    @property
    def can_scan(self):
        if self.is_pro:
            return True
        return self.monthly_scans < 30

    @property
    def can_upload_image(self):
        return self.is_pro