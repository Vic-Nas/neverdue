# accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html

from .models import MonthlyUsage, User

# ---------------------------------------------------------------------------
# Pricing constants — update here if Anthropic changes rates.
# USD per million tokens for claude-sonnet-4.
# ---------------------------------------------------------------------------
INPUT_COST_PER_MILLION = 3.00
OUTPUT_COST_PER_MILLION = 15.00


class MonthlyUsageInline(admin.TabularInline):
    model = MonthlyUsage
    extra = 0
    readonly_fields = ('year', 'month', 'input_tokens', 'output_tokens', 'cost_display')
    fields = ('year', 'month', 'input_tokens', 'output_tokens', 'cost_display')
    ordering = ('-year', '-month')
    can_delete = False
    max_num = 0  # read-only history, no adding via inline

    @admin.display(description='Cost (USD)')
    def cost_display(self, obj):
        return f'${obj.cost_usd:.4f}'


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        'username', 'email', 'is_superuser', 'is_staff', 'is_active',
        'monthly_scans', 'token_usage_display', 'current_month_cost_display', 'timezone',
    )
    search_fields = ('username', 'email')
    list_filter = ('is_superuser', 'is_staff', 'is_active')
    readonly_fields = (
        'google_id', 'token_expiry',
        'gcal_channel_id', 'gcal_channel_resource_id', 'gcal_channel_expiration',
        'date_joined', 'last_login',
        'monthly_input_tokens', 'monthly_output_tokens',
    )
    inlines = [MonthlyUsageInline]

    fieldsets = UserAdmin.fieldsets + (
        ('Google', {
            'fields': ('google_id', 'google_calendar_token', 'google_refresh_token', 'token_expiry'),
        }),
        ('GCal Watch', {
            'fields': ('gcal_channel_id', 'gcal_channel_resource_id', 'gcal_channel_expiration'),
        }),
        ('Preferences', {
            'fields': (
                'language', 'timezone', 'timezone_auto_detected',
                'auto_delete_past_events', 'past_event_retention_days', 'delete_from_gcal_on_cleanup',
            ),
        }),
        ('Usage', {
            'fields': ('monthly_scans', 'scan_reset_date', 'monthly_input_tokens', 'monthly_output_tokens'),
        }),
        ('Priority Colors', {
            'fields': ('priority_color_low', 'priority_color_medium', 'priority_color_high', 'priority_color_urgent'),
        }),
    )

    @admin.display(description='Tokens (in / out)')
    def token_usage_display(self, obj):
        return f'{obj.monthly_input_tokens:,} / {obj.monthly_output_tokens:,}'

    @admin.display(description='Cost this month')
    def current_month_cost_display(self, obj):
        input_cost = (obj.monthly_input_tokens / 1_000_000) * INPUT_COST_PER_MILLION
        output_cost = (obj.monthly_output_tokens / 1_000_000) * OUTPUT_COST_PER_MILLION
        total = input_cost + output_cost
        return format_html('<span style="font-family: monospace;">${:.4f}</span>', total)


@admin.register(MonthlyUsage)
class MonthlyUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'period_display', 'input_tokens', 'output_tokens', 'cost_display')
    list_filter = ('year', 'month', 'user')
    search_fields = ('user__username', 'user__email')
    ordering = ('-year', '-month', 'user__username')
    readonly_fields = (
        'user', 'year', 'month',
        'input_tokens', 'output_tokens',
        'input_cost_per_million', 'output_cost_per_million',
        'cost_display',
    )

    def has_add_permission(self, request):
        return False  # written only by reset_monthly_scans

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description='Period', ordering='-year')
    def period_display(self, obj):
        return f'{obj.year}-{obj.month:02d}'

    @admin.display(description='Cost (USD)')
    def cost_display(self, obj):
        return format_html('<span style="font-family: monospace;">${:.4f}</span>', obj.cost_usd)