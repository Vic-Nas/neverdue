# dashboard/models.py
from django.db import models
from django.core.exceptions import ValidationError
from accounts.models import User


class Category(models.Model):
    PRIORITY_CHOICES = [
        (1, 'Low'),
        (2, 'Medium'),
        (3, 'High'),
        (4, 'Urgent'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, null=True, blank=True)  # hex color for UI display
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=1)
    gcal_color_id = models.CharField(max_length=2, blank=True, default='')  # GCal color palette ID
    reminders = models.JSONField(default=list)  # e.g. [{"minutes": 10080}, {"minutes": 60}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.gcal_color_id and self.user_id:
            from dashboard.writer import _priority_color_id
            self.gcal_color_id = _priority_color_id(self.user, self.priority)
        super().save(*args, **kwargs)


class Rule(models.Model):
    TYPE_SENDER = 'sender'
    TYPE_KEYWORD = 'keyword'
    TYPE_PROMPT = 'prompt'

    RULE_TYPES = [
        (TYPE_SENDER, 'Sender'),
        (TYPE_KEYWORD, 'Keyword'),
        (TYPE_PROMPT, 'Prompt injection'),
    ]

    ACTION_ALLOW = 'allow'
    ACTION_BLOCK = 'block'
    ACTION_CATEGORIZE = 'categorize'
    ACTION_DISCARD = 'discard'

    ACTION_CHOICES = [
        (ACTION_ALLOW, 'Allow'),
        (ACTION_BLOCK, 'Block'),
        (ACTION_CATEGORIZE, 'Categorize'),
        (ACTION_DISCARD, 'Discard'),
    ]

    # Actions that are only valid for sender-type rules
    SENDER_ONLY_ACTIONS = {ACTION_ALLOW, ACTION_BLOCK}

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rules')
    rule_type = models.CharField(max_length=20, choices=RULE_TYPES, default=TYPE_KEYWORD)
    # sender/keyword: pattern to match; prompt: optional sender scope (empty = always inject)
    pattern = models.CharField(max_length=255, blank=True)
    # sender/keyword: what to do on match
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='rules'
    )
    # prompt: the instruction text to inject into the LLM prompt
    prompt_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rule_type', 'created_at']

    def __str__(self):
        if self.rule_type == self.TYPE_PROMPT:
            return f'prompt: {self.prompt_text[:50]}'
        return f'{self.rule_type}:{self.pattern} → {self.action}'


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

    # Minimum event duration (in minutes) allowed per recurrence frequency
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
    color = models.CharField(max_length=20, blank=True, default='')  # GCal colorId, overrides category priority color
    gcal_link = models.URLField(blank=True, default='')
    scan_job = models.ForeignKey(
        'emails.ScanJob',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='events',
    )
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

    def serialize_as_text(self) -> str:
        """
        Serialize this event to a human-readable text block for LLM re-extraction.
        Times are converted to the user's local timezone so the LLM sees the
        same times the user sees.
        Used by reprocess flows in dashboard/views.py and emails/tasks.py.
        """
        import zoneinfo
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
        return self.title