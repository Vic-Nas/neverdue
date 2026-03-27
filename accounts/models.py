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

    # Preferences
    language = models.CharField(max_length=10, default='English')
    auto_delete_past_events = models.BooleanField(default=True)
    past_event_retention_days = models.IntegerField(default=30)
    delete_from_gcal_on_cleanup = models.BooleanField(default=False)

    # Timezone — stored as IANA string e.g. "America/Toronto"
    # timezone_auto_detected: False means user has manually set it (don't overwrite with JS detection)
    timezone = models.CharField(max_length=100, default='UTC')
    timezone_auto_detected = models.BooleanField(default=False)

    # GCal watch channel for push notifications
    gcal_channel_id = models.CharField(max_length=255, null=True, blank=True)
    gcal_channel_resource_id = models.CharField(max_length=255, null=True, blank=True)
    gcal_channel_expiration = models.DateTimeField(null=True, blank=True)

    # Priority colors — stored as Google Calendar colorId (1–11).
    # Defaults: Low→Sage(2), Medium→Banana(5), High→Tangerine(6), Urgent→Tomato(11)
    priority_color_low = models.IntegerField(default=2)
    priority_color_medium = models.IntegerField(default=5)
    priority_color_high = models.IntegerField(default=6)
    priority_color_urgent = models.IntegerField(default=11)

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
