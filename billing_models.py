# billing/models.py
import logging
import random
import string

import stripe
from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import User

logger = logging.getLogger(__name__)


def _generate_referral_code():
    chars = string.ascii_uppercase + string.digits
    inner = ''.join(random.choices(chars, k=5))
    return f'NVD-{inner}'


def _stripe_upsert_coupon(code, percent_off, duration, name):
    """
    Delete-then-create a Stripe Coupon by ID so sync is idempotent.
    Stripe coupons with a fixed ID cannot be updated, only recreated.

    Side-effect: when the coupon is deleted, Stripe deactivates any PromotionCodes
    that reference it. When the coupon is recreated with the same ID, Stripe
    automatically reactivates those PCs. _stripe_ensure_promotion_code therefore
    only needs to create a PC if none exists yet.
    """
    try:
        stripe.Coupon.delete(code)
    except stripe.error.InvalidRequestError:
        pass  # didn't exist — that's fine
    stripe.Coupon.create(
        id=code,
        percent_off=percent_off,
        duration=duration,
        name=name,
    )


def _stripe_ensure_promotion_code(coupon_id, code):
    """
    Ensure a PromotionCode with the given code string exists for coupon_id.

    Must be called AFTER _stripe_upsert_coupon, at which point Stripe has already
    reactivated any existing PCs that reference the (just-recreated) coupon.
    We only need to create a PC if none has ever been created for this code string.
    """
    existing = list(stripe.PromotionCode.list(code=code, limit=1).auto_paging_iter())
    if existing:
        return  # already exists (and is active — coupon was just recreated)
    stripe.PromotionCode.create(coupon=coupon_id, code=code)


class Subscription(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('trialing', 'Trialing'),
        ('cancelled', 'Cancelled'),
        ('past_due', 'Past Due'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    stripe_customer_id = models.CharField(max_length=255, unique=True)
    stripe_subscription_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='cancelled')
    current_period_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Referral
    referral_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    referral_code_generated_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_pro(self):
        return self.status in ('active', 'trialing')

    def generate_referral_code(self):
        """Lazily generate a referral code. Call only when user requests it."""
        for _ in range(10):
            code = _generate_referral_code()
            if not Subscription.objects.filter(referral_code=code).exists():
                self.referral_code = code
                self.referral_code_generated_at = timezone.now()
                self.save(update_fields=['referral_code', 'referral_code_generated_at'])
                self._sync_referral_code_to_stripe(code)
                return code
        raise RuntimeError('Could not generate unique referral code')

    def _sync_referral_code_to_stripe(self, code):
        """Push a referral code to Stripe as a Coupon + PromotionCode so it's usable at checkout."""
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            _stripe_upsert_coupon(
                code=code,
                percent_off=12,  # display value; actual discount computation uses 12.5 float
                duration='forever',
                name=f'Referral discount ({code})',
            )
            _stripe_ensure_promotion_code(coupon_id=code, code=code)
        except stripe.error.StripeError:
            logger.exception('_sync_referral_code_to_stripe: failed to sync %s to Stripe', code)


class Coupon(models.Model):
    """Staff-created percent discount coupons, mirrored to Stripe on creation."""
    code = models.CharField(max_length=50, unique=True)
    percent = models.PositiveSmallIntegerField()  # 1–100
    label = models.CharField(max_length=255)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.code} ({self.percent}%)'

    def is_redeemable(self):
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def sync_to_stripe(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        _stripe_upsert_coupon(
            code=self.code,
            percent_off=self.percent,
            duration='forever',
            name=self.label,
        )
        _stripe_ensure_promotion_code(coupon_id=self.code, code=self.code)


class CouponRedemption(models.Model):
    """Records a user redeeming a percent coupon. Persists even after coupon expires."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coupon_redemptions')
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name='redemptions')
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'coupon')

    def __str__(self):
        return f'{self.user.username} — {self.coupon.code}'