# support/models.py
import uuid
from django.db import models
from django.conf import settings


# Mailboxes users should contact for sensitive request types.
# Format: address is {key}@service.neverdue.ca
CONTACT_SERVICES = {
    "privacy": "privacy",
    "billing": "billing",
    "legal":   "legal",
    "abuse":   "abuse",
}


class Ticket(models.Model):
    TYPE_BUG     = "bug"
    TYPE_FEATURE = "feature"
    TYPE_HOWTO   = "howto"
    TYPE_PERF    = "perf"
    TYPE_PRIVACY = "privacy"
    TYPE_CHOICES = [
        (TYPE_BUG,     "🐛 Something is broken"),
        (TYPE_FEATURE, "💡 Feature request"),
        (TYPE_HOWTO,   "❓ How do I…?"),
        (TYPE_PERF,    "⚡ Something is slow"),
        (TYPE_PRIVACY, "🔐 Privacy / account issue"),
    ]

    STATUS_PENDING  = "pending"
    STATUS_AWAITING = "awaiting_user"
    STATUS_OPEN     = "open"
    STATUS_CLOSED   = "closed"
    STATUS_CHOICES  = [
        (STATUS_PENDING,  "Pending"),
        (STATUS_AWAITING, "Awaiting user"),
        (STATUS_OPEN,     "Open"),
        (STATUS_CLOSED,   "Closed"),
    ]

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name="support_tickets",
    )
    type       = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_BUG)
    body       = models.TextField()
    llm_answer = models.TextField(blank=True)
    gh_url     = models.URLField(blank=True)
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ticket({self.type}, {self.status}, {self.id})"