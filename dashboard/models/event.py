# dashboard/models/event.py
import zoneinfo

from django.core.exceptions import ValidationError
from django.db import models
from accounts.models import User
from .category import Category


class Event(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('active', 'Active'),
    ]

    RECURRENCE_CHOICES = [
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('YEARLY', 'Yearly'),
    ]

    RECURRENCE_MIN_INTERVAL = {
        'DAILY': 1,
        'WEEKLY': 7,
        'MONTHLY': 30,
        'YEARLY': 365,
    }

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.CASCADE, related_name='events')
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    recurrence_freq = models.CharField(max_length=10, choices=RECURRENCE_CHOICES, null=True, blank=True)
    recurrence_until = models.DateField(null=True, blank=True)
    google_event_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    source_email_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    pending_expires_at = models.DateField(null=True, blank=True)
    pending_concern = models.TextField(null=True, blank=True)
    color = models.CharField(max_length=20, blank=True, default='')
    reminders = models.JSONField(default=list, blank=True)
    links = models.JSONField(default=list, blank=True)
    gcal_link = models.URLField(blank=True, default='')
    scan_job = models.ForeignKey(
        'emails.ScanJob', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='events',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    private = models.BooleanField(default=True)

    class Meta:
        app_label = 'dashboard'

    def clean(self):
        if self.start and self.end:
            if self.end <= self.start:
                raise ValidationError('End time must be after start time.')
            if self.recurrence_freq:
                duration_days = (self.end - self.start).total_seconds() / 86400
                min_days = self.RECURRENCE_MIN_INTERVAL[self.recurrence_freq]
                if duration_days >= min_days:
                    raise ValidationError(
                        f'A {self.recurrence_freq.lower()} recurring event cannot be '
                        f'{duration_days:.1f} day(s) long — event duration must be '
                        f'shorter than the recurrence interval.'
                    )
        if self.recurrence_until and self.start:
            if self.recurrence_until <= self.start.date():
                raise ValidationError('Recurrence end date must be after the event start date.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def rrule(self) -> str | None:
        if not self.recurrence_freq:
            return None
        rule = f'RRULE:FREQ={self.recurrence_freq}'
        if self.recurrence_until:
            rule += f';UNTIL={self.recurrence_until.strftime("%Y%m%d")}'
        return rule

    def serialize_as_text(self) -> str:
        user_tz_name = getattr(self.user, 'timezone', 'UTC') if self.user else 'UTC'
        try:
            user_tz = zoneinfo.ZoneInfo(user_tz_name)
        except (zoneinfo.ZoneInfoNotFoundError, KeyError):
            user_tz = zoneinfo.ZoneInfo('UTC')
        local_start = self.start.astimezone(user_tz)
        local_end = self.end.astimezone(user_tz)
        lines = [
            f"Title: {self.title}",
            f"Start: {local_start.strftime('%Y-%m-%dT%H:%M:%S')}",
            f"End: {local_end.strftime('%Y-%m-%dT%H:%M:%S')}",
        ]
        if self.description:
            lines.append(f"Notes: {self.description}")
        if self.links:
            for link in self.links:
                label = link.get('title') or link.get('url', '')
                lines.append(f"Link: {label} — {link.get('url', '')}")
        if self.recurrence_freq:
            lines.append(f"Recurrence: {self.recurrence_freq}")
            if self.recurrence_until:
                lines.append(f"Recurrence until: {self.recurrence_until}")
        if self.category:
            lines.append(f"Category: {self.category.name}")
        if self.pending_concern:
            lines.append(f"Previous concern: {self.pending_concern}")
        return "\n".join(lines)

    def __str__(self):
        return f'{self.title} ({self.start})'