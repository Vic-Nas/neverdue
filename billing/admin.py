# billing/admin.py
from django.contrib import admin
from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'stripe_customer_id', 'stripe_subscription_id', 'current_period_end', 'created_at')
    search_fields = ('user__username', 'user__email', 'stripe_customer_id', 'stripe_subscription_id')
    list_filter = ('status',)
    readonly_fields = ('created_at',)