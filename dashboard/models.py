# dashboard/models.py
from django.db import models
from django.core.exceptions import ValidationError
from accounts.models import User


class Category(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, null=True, blank=True)  # hex color
    reminders = models.JSONField(default=list)  # e.g. [{"minutes": 10080}, {"minutes": 60}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'name']

    def __str__(self):
        return self.name


class Rule(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rules')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='rules')
    sender = models.CharField(max_length=255, null=True, blank=True)
    keyword = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        parts = []
        if self.sender:
            parts.append(f'from:{self.sender}')
        if self.keyword:
            parts.append(f'contains:{self.keyword}')
        return ' | '.join(parts) or 'Empty rule'


class Event(models.Model):
    RECURRENCE_CHOICES = [
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('YEARLY', 'Yearly'),
    ]

    # Minimum event duration (in minutes) allowed per recurrence frequency
    RECURRENCE_MIN_INTERVAL = {
        'DAILY': 1,
        'WEEKLY': 7,
        'MONTHLY': 30,
        'YEARLY': 365,
    }

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='events')
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    recurrence_freq = models.CharField(max_length=10, choices=RECURRENCE_CHOICES, null=True, blank=True)
    recurrence_until = models.DateField(null=True, blank=True)
    google_event_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    source_email_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.start and self.end:
            if self.end <= self.start:
                raise ValidationError('End time must be after start time.')

            if self.recurrence_freq:
                duration_days = (self.end - self.start).total_seconds() / 86400
                min_days = self.RECURRENCE_MIN_INTERVAL[self.recurrence_freq]
                if duration_days >= min_days:
                    raise ValidationError(
                        f'A {self.recurrence_freq.lower()} recurring event cannot be {duration_days:.1f} day(s) long — '
                        f'event duration must be shorter than the recurrence interval.'
                    )

        if self.recurrence_until and self.start:
            if self.recurrence_until <= self.start.date():
                raise ValidationError('Recurrence end date must be after the event start date.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def rrule(self) -> str | None:
        """Generate RRULE string for Google Calendar API."""
        if not self.recurrence_freq:
            return None
        rule = f'RRULE:FREQ={self.recurrence_freq}'
        if self.recurrence_until:
            until = self.recurrence_until.strftime('%Y%m%d')
            rule += f';UNTIL={until}'
        return rule

    def __str__(self):
        return self.title