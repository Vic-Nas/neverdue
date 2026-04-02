from django.db import models
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
    color = models.CharField(max_length=7, null=True, blank=True)
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=1)
    gcal_color_id = models.CharField(max_length=2, blank=True, default='')
    reminders = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'dashboard'
        unique_together = ['user', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.gcal_color_id and self.user_id:
            from dashboard.writer import _priority_color_id
            self.gcal_color_id = _priority_color_id(self.user, self.priority)
        super().save(*args, **kwargs)
