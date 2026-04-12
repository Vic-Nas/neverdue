# billing/models.py
import logging
import math
import random
import string

import stripe
from django.conf import settings
from django.db import models

from accounts.models import User

logger = logging.getLogger(__name__)


def compute_discount(user):
    """
    Total refund % this user will receive next month-end.

    As a redeemer: flat coupon.percent per coupon, if head paid (or head=None).
    As a head: coupon.percent * count(redeemers who are active) per coupon.
    Both sides stack; result is capped at 100 and ceiling'd to int.
    """
    total = 0.0

    # Redeemer side
    for redemption in (
        user.redemptions
        .select_related('coupon__head__subscription')
    ):
        coupon = redemption.coupon
        head = coupon.head
        head_ok = (
            head is None or
            (hasattr(head, 'subscription') and head.subscription.status == 'active')
        )
        if head_ok:
            total += float(coupon.percent)

    # Head side
    for coupon in (
        Coupon.objects
        .filter(head=user)
        .prefetch_related('redemptions__user__subscription')
    ):
        active_redeemers = sum(
            1 for r in coupon.redemptions.all()
            if hasattr(r.user, 'subscription') and r.user.subscription.status == 'active'
        )
        total += float(coupon.percent) * active_redeemers

    return min(math.ceil(total), 100)


class Coupon(models.Model):
    """
    A discount coupon created by staff and pushed to Stripe on save.

    head=None means NeverDue is the sponsor (staff grant); redeemers always
    receive their refund unconditionally.
    head=<user> means that user receives percent*active_redeemers as their
    own refund each month.

    Referral coupons (auto-generated per user) are Coupon rows with
    head=that user, percent=12.5, max_redemptions=12.
    """
    code = models.CharField(max_length=50, unique=True)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    max_redemptions = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Leave blank for unlimited. Enforced by Stripe at checkout.',
    )
    head = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='headed_coupons',
        help_text='User who earns a refund when their redeemers pay. '
                  'Null = NeverDue grant (always pays out to redeemers).',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Stripe IDs stored after push
    stripe_coupon_id = models.CharField(max_length=255, blank=True)
    stripe_promotion_code_id = models.CharField(max_length=255, blank=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self._push_to_stripe()

    def _push_to_stripe(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        coupon_id = f'nvd-{self.code.lower()}'
        kwargs = dict(
            id=coupon_id,
            percent_off=float(self.percent),
            duration='forever',
        )
        stripe_coupon = stripe.Coupon.create(**kwargs)
        promo_kwargs = dict(coupon=stripe_coupon.id, code=self.code)
        if self.max_redemptions is not None:
            promo_kwargs['max_redemptions'] = self.max_redemptions
        promo = stripe.PromotionCode.create(**promo_kwargs)
        Coupon.objects.filter(pk=self.pk).update(
            stripe_coupon_id=stripe_coupon.id,
            stripe_promotion_code_id=promo.id,
        )

    def __str__(self):
        return f'{self.code} ({self.percent}%)'


class CouponRedemption(models.Model):
    """
    Records that a user subscribed using a specific coupon code.

    Created by the customer.discount.created webhook signal.
    Deleted when the user unsubscribes (customer.subscription.deleted).

    One row per (coupon, user) — a user can only redeem a coupon once.
    A user may have at most one active redemption at a time in practice
    (they can only enter one code at checkout), but the model does not
    enforce a hard limit to allow staff corrections.
    """
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='redemptions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='redemptions')
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('coupon', 'user')

    def __str__(self):
        return f'{self.user.username} → {self.coupon.code}'


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

    # FK to this user's own referral coupon (head=user, generated on demand).
    referral_coupon = models.OneToOneField(
        Coupon,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='referral_subscription',
    )

    @property
    def is_pro(self):
        return self.status in ('active', 'trialing')

    @property
    def referral_code(self):
        return self.referral_coupon.code if self.referral_coupon else None

    def generate_referral_code(self):
        """
        Lazily create this user's personal referral Coupon (head=self.user,
        percent=12.5, max_redemptions=12). Idempotent.
        Returns the NVD-XXXXX code string.
        """
        if self.referral_coupon_id:
            return self.referral_coupon.code

        chars = string.ascii_uppercase + string.digits
        for _ in range(10):
            code = 'NVD-' + ''.join(random.choices(chars, k=5))
            if not Coupon.objects.filter(code=code).exists():
                coupon = Coupon.objects.create(
                    code=code,
                    percent='12.50',
                    max_redemptions=12,
                    head=self.user,
                )
                self.referral_coupon = coupon
                self.save(update_fields=['referral_coupon'])
                return code

        raise RuntimeError('Could not generate a unique referral code after 10 attempts')


class RefundRecord(models.Model):
    """
    Idempotency guard for monthly refunds.
    One row per (CouponRedemption, Stripe invoice) for redeemer refunds,
    and one row per (Coupon-as-head, Stripe invoice) for head refunds.
    """
    # Exactly one of these two FKs is set per row.
    redemption = models.ForeignKey(
        CouponRedemption,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name='refunds',
    )
    coupon_head = models.ForeignKey(
        Coupon,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name='head_refunds',
    )
    stripe_invoice_id = models.CharField(max_length=255)
    stripe_refund_id = models.CharField(max_length=255)
    amount = models.PositiveIntegerField()  # cents
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['redemption', 'stripe_invoice_id'],
                condition=models.Q(redemption__isnull=False),
                name='unique_redemption_invoice',
            ),
            models.UniqueConstraint(
                fields=['coupon_head', 'stripe_invoice_id'],
                condition=models.Q(coupon_head__isnull=False),
                name='unique_head_invoice',
            ),
        ]

    def __str__(self):
        if self.redemption_id:
            return f'RefundRecord(redemption={self.redemption_id} invoice={self.stripe_invoice_id})'
        return f'RefundRecord(head_coupon={self.coupon_head_id} invoice={self.stripe_invoice_id})'