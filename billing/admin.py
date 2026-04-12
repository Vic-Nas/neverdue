# billing/admin.py
from django.contrib import admin

from accounts.models import User
from billing.models import RefundRecord, Subscription, UserCoupon


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'referral_code', 'referral_max_redemptions',
                    'current_period_end')
    list_filter = ('status',)
    search_fields = ('user__email', 'user__username', 'stripe_customer_id')
    readonly_fields = ('stripe_customer_id', 'stripe_subscription_id', 'created_at')


@admin.register(UserCoupon)
class UserCouponAdmin(admin.ModelAdmin):
    """
    Staff creates staff-grant coupons here: add the target user + admin sentinel.
    Referral coupons are created automatically by the signal handler — do not
    create them manually unless correcting data.

    Discounts are issued as month-end refunds by process_monthly_refunds — there
    is no Stripe-side coupon to sync here.
    """
    list_display = ('id', 'percent', 'user_list', 'created_at')
    readonly_fields = ('created_at',)
    filter_horizontal = ('users',)

    def user_list(self, obj):
        return ', '.join(u.username for u in obj.users.all())
    user_list.short_description = 'Users'


@admin.register(RefundRecord)
class RefundRecordAdmin(admin.ModelAdmin):
    """
    Read-only financial audit log. Staff can inspect but not modify or delete.
    """
    list_display = ('user_coupon', 'stripe_invoice_id', 'amount', 'created_at')
    search_fields = ('stripe_invoice_id', 'stripe_refund_id')
    readonly_fields = (
        'user_coupon', 'stripe_invoice_id', 'stripe_refund_id', 'amount', 'created_at'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User, site=admin.site)
class UserReferralAdmin(admin.ModelAdmin):
    """
    Minimal read-only User view — only safe fields, no tokens, no sensitive data.
    """
    list_display = ('username', 'email', 'date_joined', 'is_pro_display')
    search_fields = ('username', 'email')
    readonly_fields = ('username', 'email', 'date_joined')
    exclude = (
        'password', 'google_id', 'google_calendar_token', 'google_refresh_token',
        'token_expiry', 'gcal_channel_id', 'gcal_channel_resource_id',
        'gcal_channel_expiration', 'user_permissions', 'groups',
    )

    def is_pro_display(self, obj):
        return obj.is_pro
    is_pro_display.boolean = True
    is_pro_display.short_description = 'Pro'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
