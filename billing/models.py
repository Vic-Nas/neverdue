# billing/models.py
import logging
import random
import string

import stripe
from django.conf import settings
from django.db import models

from accounts.models import User

logger = logging.getLogger(__name__)


def compute_discount(user):
    """
    Sum percent of all UserCoupons for this user where every other user
    on the coupon is active (status='active') or is the admin sentinel.
    Cap at 100, return int.
    """
    try:
        admin = User.objects.get(username='admin')
    except User.DoesNotExist:
        admin = None

    total = 0.0
    for coupon in user.coupons.prefetch_related('users__subscription'):
        others = [u for u in coupon.users.all() if u != user]
        if all(
            (admin and u.pk == admin.pk) or
            (hasattr(u, 'subscription') and u.subscription.status == 'active')
            for u in others
        ):
            total += float(coupon.percent)

    return min(int(total), 100)


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

    # Referral code — generated on demand, used to identify the referrer in
    # customer.discount.created webhook. Permanent once created.
    referral_code = models.CharField(max_length=20, unique=True, null=True, blank=True)

    @property
    def is_pro(self):
        return self.status in ('active', 'trialing')


    def generate_referral_code(self):
        """
        Generate a unique NVD-XXXXX code, save it, and push it to Stripe as a
        PromotionCode on the shared referral coupon. Idempotent: returns the
        existing code if one already exists.
        """
        if self.referral_code:
            return self.referral_code

        chars = string.ascii_uppercase + string.digits
        for _ in range(10):
            code = 'NVD-' + ''.join(random.choices(chars, k=5))
            if not Subscription.objects.filter(referral_code=code).exists():
                self.referral_code = code
                self.save(update_fields=['referral_code'])
                try:
                    stripe.api_key = settings.STRIPE_SECRET_KEY
                    stripe.PromotionCode.create(
                        promotion=settings.STRIPE_REFERRAL_COUPON_ID,
                        code=code,
                    )
                except stripe.error.StripeError:
                    logger.exception(
                        'generate_referral_code: failed to create PromotionCode | code=%s', code
                    )
                return code

        raise RuntimeError('Could not generate a unique referral code after 10 attempts')


class UserCoupon(models.Model):
    """
    A discount shared between users.

    Each user on the coupon gets `percent` off while all other users on the
    same coupon are active (status='active') or are the admin sentinel.

    Referral: two users. Both get 12.5% while both pay.
    Staff grant: one user + admin sentinel. User always gets the discount.

    Redemption limits are enforced by Stripe on the PromotionCode;
    we do not shadow-implement them here.
    """
    users = models.ManyToManyField(User, related_name='coupons')
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        user_list = ', '.join(u.username for u in self.users.all())
        return f'UserCoupon({self.percent}% — {user_list})'


class RefundRecord(models.Model):
    """
    Idempotency guard for monthly refunds.
    One row per (UserCoupon, Stripe invoice).
    Prevents double refund if Procrastinate retries the job.
    """
    user_coupon = models.ForeignKey(
        UserCoupon, on_delete=models.PROTECT, related_name='refunds'
    )
    stripe_invoice_id = models.CharField(max_length=255)
    stripe_refund_id = models.CharField(max_length=255)
    amount = models.PositiveIntegerField()  # cents
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user_coupon', 'stripe_invoice_id')

    def __str__(self):
        return f'RefundRecord(coupon={self.user_coupon_id} invoice={self.stripe_invoice_id})'
