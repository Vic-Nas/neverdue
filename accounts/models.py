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

    # LLM token usage — current month rolling counters.
    # Reset to 0 each month by reset_monthly_scans (after snapshotting to MonthlyUsage).
    monthly_input_tokens = models.PositiveBigIntegerField(default=0)
    monthly_output_tokens = models.PositiveBigIntegerField(default=0)

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


class MonthlyUsage(models.Model):
    """
    Per-user monthly LLM token usage snapshot.
    Written by reset_monthly_scans before clearing the rolling counters on User.
    Never modified after creation — treat as append-only history.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='monthly_usage')
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)

    # Pricing snapshot at time of record — lets us recalculate cost later if rates change.
    # Stored as USD per million tokens (e.g. 3.00 and 15.00 for Sonnet).
    input_cost_per_million = models.DecimalField(max_digits=8, decimal_places=4, default='3.0000')
    output_cost_per_million = models.DecimalField(max_digits=8, decimal_places=4, default='15.0000')

    class Meta:
        unique_together = ('user', 'year', 'month')
        ordering = ('-year', '-month')

    def __str__(self):
        return f"{self.user.username} — {self.year}-{self.month:02d}"

    @property
    def cost_usd(self) -> float:
        input_cost = (self.input_tokens / 1_000_000) * float(self.input_cost_per_million)
        output_cost = (self.output_tokens / 1_000_000) * float(self.output_cost_per_million)
        return round(input_cost + output_cost, 6)