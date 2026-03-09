from django.db import models
from accounts.models import User


class Category(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, null=True, blank=True)  # hex color
    reminders = models.JSONField(default=list)  # e.g. [{"minutes": 10080}, {"minutes": 60}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'name']


class Rule(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rules')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='rules')
    sender = models.CharField(max_length=255, null=True, blank=True)
    keyword = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Event(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='events')
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    google_event_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    source_email_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)