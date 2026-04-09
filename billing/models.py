# billing/models.py
import random
import string

import stripe
from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import User


def _generate_referral_code():
    chars = string.ascii_uppercase + string.digits
    inner = ''.join(random.choices(chars, k=5))
    return f'NVD-{inner}'


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
                return code
        raise RuntimeError('Could not generate unique referral code')


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
        stripe.Coupon.create(
            id=self.code,
            percent_off=self.percent,
            duration='forever',
            name=self.label,
        )


class CouponRedemption(models.Model):
    """Records a user redeeming a percent coupon. Persists even after coupon expires."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coupon_redemptions')
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name='redemptions')
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'coupon')

    def __str__(self):
        return f'{self.user.username} — {self.coupon.code}'
