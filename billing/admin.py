# billing/admin.py
import stripe
from django.conf import settings
from django.contrib import admin

from accounts.models import User
from billing.models import Coupon, CouponRedemption, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'referral_code', 'current_period_end')
    list_filter = ('status',)
    search_fields = ('user__email', 'user__username', 'stripe_customer_id')
    readonly_fields = ('stripe_customer_id', 'stripe_subscription_id',
                       'created_at', 'referral_code_generated_at')


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'percent', 'label', 'expires_at', 'redemption_count', 'created_at')
    search_fields = ('code', 'label')
    readonly_fields = ('created_at',)

    def redemption_count(self, obj):
        return obj.redemptions.count()
    redemption_count.short_description = 'Redemptions'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            # Mirror to Stripe on first creation only
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                obj.sync_to_stripe()
            except stripe.error.StripeError as exc:
                self.message_user(
                    request,
                    f'Coupon saved locally but Stripe sync failed: {exc}',
                    level='WARNING',
                )


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'coupon', 'redeemed_at')
    list_filter = ('coupon',)
    search_fields = ('user__email', 'user__username', 'coupon__code')
    readonly_fields = ('redeemed_at',)


class UserReferralAdmin(admin.ModelAdmin):
    """
    Minimal read-only User view — only safe fields, no tokens.
    Registered separately to avoid overriding the full auth.User admin.
    """
    list_display = ('username', 'email', 'date_joined', 'is_pro_display', 'referred_by')
    search_fields = ('username', 'email')
    readonly_fields = ('username', 'email', 'date_joined', 'referred_by')
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


admin.site.register(User, UserReferralAdmin)
