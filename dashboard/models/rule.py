# dashboard/models/rule.py
from django.db import models
from accounts.models import User
from .category import Category


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

    SENDER_ONLY_ACTIONS = {ACTION_ALLOW, ACTION_BLOCK}

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rules')
    rule_type = models.CharField(max_length=20, choices=RULE_TYPES, default=TYPE_KEYWORD)
    pattern = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='rules'
    )
    prompt_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'dashboard'
        ordering = ['rule_type', 'created_at']

    def __str__(self):
        if self.rule_type == self.TYPE_PROMPT:
            return f'prompt: {self.prompt_text[:50]}'
        return f'{self.rule_type}:{self.pattern} → {self.action}'
