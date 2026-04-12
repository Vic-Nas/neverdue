# billing/admin.py
from django.contrib import admin

from accounts.models import User
from billing.models import Coupon, CouponRedemption, RefundRecord, Subscription

# ---------------------------------------------------------------------------
# Unregister dj-stripe models that are irrelevant to NeverDue's admin UI.
# dj-stripe syncs these locally but staff should not manage them here.
# ---------------------------------------------------------------------------
_DJSTRIPE_MODELS_TO_HIDE = [
    'Coupon', 'Customer', 'Subscription', 'Invoice', 'Price', 'Product',
    'PaymentMethod', 'Charge', 'Refund', 'BalanceTransaction',
    'WebhookEventTrigger', 'PromotionCode', 'Discount',
]
for _name in _DJSTRIPE_MODELS_TO_HIDE:
    try:
        import djstripe.models as _djs
        admin.site.unregister(getattr(_djs, _name))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NeverDue billing admin
# ---------------------------------------------------------------------------

class CouponRedemptionInline(admin.TabularInline):
    model = CouponRedemption
    extra = 0
    readonly_fields = ('user', 'redeemed_at')
    can_delete = False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    """
    Staff creates all coupons here. Saving a new Coupon immediately pushes it
    to Stripe (creates the Stripe Coupon + PromotionCode).

    For YouTube / influencer deals, typical workflow:
      1. Create a 100%-forever / max_redemptions=1 coupon for the channel owner
         (head=None or head=owner depending on deal).
      2. Create a 30%-forever coupon for their audience (head=None or head=owner).

    Referral coupons (head=user, percent=12.5, max_redemptions=12) are created
    automatically when a user generates their referral code — do not create
    them manually unless correcting data.
    """
    list_display = ('code', 'percent', 'head', 'max_redemptions', 'redemption_count', 'created_at')
    list_filter = ('percent',)
    search_fields = ('code', 'head__username', 'head__email')
    readonly_fields = ('created_at', 'stripe_coupon_id', 'stripe_promotion_code_id')
    raw_id_fields = ('head',)
    inlines = [CouponRedemptionInline]

    def redemption_count(self, obj):
        return obj.redemptions.count()
    redemption_count.short_description = 'Redemptions'

    def get_readonly_fields(self, request, obj=None):
        # Once created, code and percent are immutable (Stripe is already set).
        if obj:
            return self.readonly_fields + ('code', 'percent', 'max_redemptions', 'head')
        return self.readonly_fields


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    """
    Read-only view of who redeemed which coupon.
    Created by webhook; staff should not create rows manually.
    """
    list_display = ('coupon', 'user', 'redeemed_at')
    search_fields = ('coupon__code', 'user__username', 'user__email')
    readonly_fields = ('coupon', 'user', 'redeemed_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'referral_code_display', 'current_period_end')
    list_filter = ('status',)
    search_fields = ('user__email', 'user__username', 'stripe_customer_id')
    readonly_fields = ('stripe_customer_id', 'stripe_subscription_id', 'created_at',
                       'referral_coupon')

    def referral_code_display(self, obj):
        return obj.referral_code or '—'
    referral_code_display.short_description = 'Referral code'


@admin.register(RefundRecord)
class RefundRecordAdmin(admin.ModelAdmin):
    """Read-only financial audit log."""
    list_display = ('__str__', 'stripe_invoice_id', 'amount', 'created_at')
    search_fields = ('stripe_invoice_id', 'stripe_refund_id')
    readonly_fields = (
        'redemption', 'coupon_head', 'stripe_invoice_id',
        'stripe_refund_id', 'amount', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User, site=admin.site)
class UserReferralAdmin(admin.ModelAdmin):
    """Minimal read-only User view — no tokens, no sensitive data."""
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