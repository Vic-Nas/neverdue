# billing/tests/test_discount_stack.py
"""
Tests for combined discount computation and Stripe sync integrity.

Covers:
  1. Coupon + referral stacking (the gap in existing tests)
  2. Double-redemption guard (unique_together on CouponRedemption)
  3. Coupon.delete() is blocked by PROTECT when redemptions exist
  4. compute_discount returns 0 after coupon deletion is forced (cascade path)
  5. DB discount matches what _push_combined_discount actually applies on Stripe
"""
import stripe
from django.conf import settings
from django.db import IntegrityError, transaction

from billing.discount import compute_discount
from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, make_user, s,
)
from billing.views.webhook import _push_combined_discount


class CouponReferralStack(BillingTestCase):
    """
    compute_discount sums coupon percents and referral percents correctly.
    """

    def setUp(self):
        super().setUp()
        self.referrer = make_user('stack_referrer')
        cust = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust.id)
        Subscription.objects.create(
            user=self.referrer, stripe_customer_id=cust.id,
            status='active', referral_code='NVD-STACK1',
        )
        self.coupon = Coupon.objects.create(
            code='WF-STACK-20', percent=20, label='Stack 20%'
        )

    def _make_referred(self, username, status='active'):
        u = make_user(username)
        cust = create_stripe_customer(u.email, u.username)
        self.track('customer', cust.id)
        Subscription.objects.create(user=u, stripe_customer_id=cust.id, status=status)
        u.referred_by = self.referrer
        u.save()
        return u

    def test_coupon_plus_one_referral(self):
        # 20 + 12.5 = 32.5 → int = 32
        CouponRedemption.objects.create(user=self.referrer, coupon=self.coupon)
        self._make_referred('stack_r1')
        self.assertEqual(compute_discount(self.referrer), 32)

    def test_coupon_plus_two_referrals(self):
        # 20 + 2 * 12.5 = 45.0 → 45
        CouponRedemption.objects.create(user=self.referrer, coupon=self.coupon)
        self._make_referred('stack_r2a')
        self._make_referred('stack_r2b')
        self.assertEqual(compute_discount(self.referrer), 45)

    def test_two_coupons_plus_referral(self):
        coupon2 = Coupon.objects.create(code='WF-STACK-10', percent=10, label='Stack 10%')
        CouponRedemption.objects.create(user=self.referrer, coupon=self.coupon)
        CouponRedemption.objects.create(user=self.referrer, coupon=coupon2)
        self._make_referred('stack_r3')
        # 20 + 10 + 12.5 = 42.5 → 42
        self.assertEqual(compute_discount(self.referrer), 42)

    def test_stack_caps_at_100(self):
        # 20% coupon + 7 active referrals = 20 + 87.5 = 107.5 → capped at 100
        CouponRedemption.objects.create(user=self.referrer, coupon=self.coupon)
        for i in range(7):
            self._make_referred(f'stack_cap{i}')
        self.assertEqual(compute_discount(self.referrer), 100)

    def test_trialing_referral_does_not_count_in_stack(self):
        # Coupon counts, trialing referred does not
        CouponRedemption.objects.create(user=self.referrer, coupon=self.coupon)
        self._make_referred('stack_trial', status='trialing')
        self.assertEqual(compute_discount(self.referrer), 20)

    def test_coupon_percent_change_reflected_immediately(self):
        # compute_discount reads coupon.percent live — editing percent takes effect
        # at next compute without any extra sync step
        CouponRedemption.objects.create(user=self.referrer, coupon=self.coupon)
        self.assertEqual(compute_discount(self.referrer), 20)
        self.coupon.percent = 30
        self.coupon.save()
        self.assertEqual(compute_discount(self.referrer), 30)


class CouponRedemptionIntegrity(BillingTestCase):
    """
    unique_together on CouponRedemption prevents double-counting.
    Coupon.delete() is blocked by PROTECT when redemptions exist.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('integrity_user')
        cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', cust.id)
        Subscription.objects.create(
            user=self.user, stripe_customer_id=cust.id, status='active'
        )
        self.coupon = Coupon.objects.create(
            code='WF-INTEG-15', percent=15, label='Integrity test'
        )

    def test_double_redemption_raises_integrity_error(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CouponRedemption.objects.create(user=self.user, coupon=self.coupon)

    def test_get_or_create_is_idempotent(self):
        CouponRedemption.objects.get_or_create(user=self.user, coupon=self.coupon)
        CouponRedemption.objects.get_or_create(user=self.user, coupon=self.coupon)
        self.assertEqual(
            CouponRedemption.objects.filter(user=self.user, coupon=self.coupon).count(), 1
        )
        self.assertEqual(compute_discount(self.user), 15)

    def test_delete_coupon_blocked_when_redemption_exists(self):
        """PROTECT means you cannot delete a redeemed coupon — it is never stale."""
        from django.db.models import ProtectedError
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.coupon.delete()

    def test_delete_coupon_allowed_when_no_redemptions(self):
        """Unredeemed coupons can be freely deleted — no orphan risk."""
        coupon_id = self.coupon.pk
        self.coupon.delete()
        self.assertFalse(Coupon.objects.filter(pk=coupon_id).exists())

    def test_discount_zero_after_redemption_deleted(self):
        """
        If a redemption is manually removed (e.g. admin correction),
        compute_discount immediately reflects the change — no stale state.
        """
        r = CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        self.assertEqual(compute_discount(self.user), 15)
        r.delete()
        self.assertEqual(compute_discount(self.user), 0)


class StripeDiscountSync(BillingTestCase):
    """
    compute_discount() matches what _push_combined_discount actually applies
    on the Stripe subscription. Verifies DB state → Stripe coupon is in sync.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('sync_user')
        cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', cust.id)
        pm = s().PaymentMethod.create(type='card', card={'token': 'tok_visa'})
        s().PaymentMethod.attach(pm.id, customer=cust.id)
        s().Customer.modify(cust.id, invoice_settings={'default_payment_method': pm.id})
        stripe_sub = s().Subscription.create(
            customer=cust.id,
            items=[{'price': settings.STRIPE_PRICE_ID}],
            trial_period_days=7,
        )
        self.track('subscription', stripe_sub.id)
        self.auto_id = f'nvd-auto-{self.user.pk}'
        self.track('coupon', self.auto_id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            stripe_subscription_id=stripe_sub.id,
            status='trialing',
        )
        self.coupon = Coupon.objects.create(
            code='WF-SYNC-25', percent=25, label='Sync 25%'
        )

    def _stripe_applied_pct(self):
        """Return the percent_off of the discount currently applied on Stripe, or 0."""
        sub = s().Subscription.retrieve(
            self.local_sub.stripe_subscription_id,
            expand=['discount'],
        )
        disc = sub.get('discount')
        if not disc:
            return 0
        return int(disc['coupon']['percent_off'])

    def _make_referred(self, username):
        u = make_user(username)
        cust = create_stripe_customer(u.email, u.username)
        self.track('customer', cust.id)
        Subscription.objects.create(user=u, stripe_customer_id=cust.id, status='active')
        u.referred_by = self.user
        u.save()
        return u

    def test_coupon_only_syncs_to_stripe(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        expected = compute_discount(self.user)
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), expected)
        self.assertEqual(expected, 25)

    def test_referral_only_syncs_to_stripe(self):
        self._make_referred('sync_ref1')
        expected = compute_discount(self.user)
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), expected)
        self.assertEqual(expected, 12)  # int(12.5)

    def test_stack_syncs_to_stripe(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        self._make_referred('sync_ref2')
        expected = compute_discount(self.user)  # 25 + 12.5 = 37
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), expected)
        self.assertEqual(expected, 37)

    def test_zero_discount_removes_stripe_discount(self):
        # First apply something, then push zero — Stripe discount must be gone
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), 25)

        r = CouponRedemption.objects.get(user=self.user, coupon=self.coupon)
        r.delete()
        self.assertEqual(compute_discount(self.user), 0)
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), 0)

    def test_coupon_percent_edit_syncs_to_stripe(self):
        # Edit coupon percent → push → Stripe reflects new value
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), 25)

        self.coupon.percent = 40
        self.coupon.save()
        expected = compute_discount(self.user)  # 40
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), expected)
        self.assertEqual(expected, 40)

    def test_second_push_is_idempotent(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        _push_combined_discount(self.local_sub)
        _push_combined_discount(self.local_sub)
        self.assertEqual(self._stripe_applied_pct(), 25)
