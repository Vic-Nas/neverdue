# accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'is_superuser', 'is_staff', 'is_active', 'monthly_scans', 'timezone')
    search_fields = ('username', 'email')
    list_filter = ('is_superuser', 'is_staff', 'is_active')
    readonly_fields = ('google_id', 'token_expiry', 'gcal_channel_id', 'gcal_channel_resource_id', 'gcal_channel_expiration', 'date_joined', 'last_login')

    fieldsets = UserAdmin.fieldsets + (
        ('Google', {
            'fields': ('google_id', 'google_calendar_token', 'google_refresh_token', 'token_expiry'),
        }),
        ('GCal Watch', {
            'fields': ('gcal_channel_id', 'gcal_channel_resource_id', 'gcal_channel_expiration'),
        }),
        ('Preferences', {
            'fields': ('language', 'timezone', 'timezone_auto_detected', 'auto_delete_past_events', 'past_event_retention_days', 'delete_from_gcal_on_cleanup'),
        }),
        ('Usage', {
            'fields': ('monthly_scans', 'scan_reset_date'),
        }),
        ('Priority Colors', {
            'fields': ('priority_color_low', 'priority_color_medium', 'priority_color_high', 'priority_color_urgent'),
        }),
    )