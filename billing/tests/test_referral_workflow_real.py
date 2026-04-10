# billing/tests/test_referral_workflow_real.py
"""
Real-Stripe tests for the full referral workflow.
No mocks. Every test talks to Stripe test mode.

Bugs this exposes:
  1. Referral codes are never synced to Stripe as coupons/promo codes —
     so they cannot be applied at checkout (Stripe won't recognise them).
  2. The NVD-XXXXX code only works in _handle_discount_created because
     we match by referral_code field, not via Stripe lookup. That part works.
     But the user can never actually type the code at checkout — Stripe
     has never heard of it.
  3. Two active referrals = 25% (2 * 12.5 truncated to int) — verify the
     int truncation is intentional vs a rounding bug.
"""
import stripe
from django.conf import settings

from billing.discount import compute_discount
from billing.models import Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, make_user, s,
)
from billing.views.webhook import _handle_discount_created


class ReferralCodeExistsOnStripe(BillingTestCase):
    """
    After generate_referral_code(), the NVD-XXXXX code must exist on Stripe
    as a Coupon + PromotionCode so it's usable at checkout.
    BUG: generate_referral_code() only saves locally, never touches Stripe.
    """

    def setUp(self):
        super().setUp()
        self.referrer = make_user('ref_stripe_check')
        cust = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust.id)
        self.referrer_sub = Subscription.objects.create(
            user=self.referrer,
            stripe_customer_id=cust.id,
            status='active',
        )

    def test_generate_code_is_nvd_format(self):
        code = self.referrer_sub.generate_referral_code()
        self.assertRegex(code, r'^NVD-[A-Z0-9]{5}$')

    def test_referral_code_synced_as_stripe_coupon(self):
        # BUG: this fails — generate_referral_code never calls Stripe
        code = self.referrer_sub.generate_referral_code()
        self.track('coupon', code)
        sc = s().Coupon.retrieve(code)
        self.assertIsNotNone(sc, 'Referral code not synced to Stripe as a coupon')

    def test_referral_code_synced_as_promotion_code(self):
        # BUG: fails for same reason — no Stripe promo code created
        code = self.referrer_sub.generate_referral_code()
        self.track('coupon', code)
        codes = s().PromotionCode.list(code=code, limit=5)
        active = [pc for pc in codes.auto_paging_iter() if pc.active]
        self.assertEqual(len(active), 1, 'Referral code not usable at Stripe checkout')

    def test_second_generate_call_does_not_duplicate_on_stripe(self):
        # After fix: calling twice should not crash Stripe with duplicate coupon id
        code1 = self.referrer_sub.generate_referral_code()
        self.track('coupon', code1)
        code2 = self.referrer_sub.generate_referral_code()
        if code1 != code2:
            self.track('coupon', code2)
        # Both should be valid NVD codes (may differ or be same — document behaviour)
        self.assertRegex(code2, r'^NVD-[A-Z0-9]{5}$')


class ReferralWebhookSetsReferredBy(BillingTestCase):
    """
    _handle_discount_created with a referral code coupon_id sets referred_by.
    This path works today because we match by local referral_code field.
    """

    def setUp(self):
        super().setUp()
        self.referrer = make_user('ref_wh_referrer')
        cust_r = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust_r.id)
        Subscription.objects.create(
            user=self.referrer, stripe_customer_id=cust_r.id,
            status='active', referral_code='NVD-ABCDE',
        )
        self.new_user = make_user('ref_wh_new')
        cust_n = create_stripe_customer(self.new_user.email, self.new_user.username)
        self.track('customer', cust_n.id)
        self.new_sub = Subscription.objects.create(
            user=self.new_user, stripe_customer_id=cust_n.id, status='cancelled',
        )

    def _discount_obj(self, code):
        return {'coupon': {'id': code}, 'customer': self.new_sub.stripe_customer_id}

    def test_known_referral_code_sets_referred_by(self):
        _handle_discount_created(self._discount_obj('NVD-ABCDE'))
        self.new_user.refresh_from_db()
        self.assertEqual(self.new_user.referred_by, self.referrer)

    def test_referred_by_not_overwritten_second_event(self):
        _handle_discount_created(self._discount_obj('NVD-ABCDE'))
        other = make_user('ref_wh_other')
        cust_o = create_stripe_customer(other.email, other.username)
        self.track('customer', cust_o.id)
        Subscription.objects.create(
            user=other, stripe_customer_id=cust_o.id,
            status='active', referral_code='NVD-ZZZZZ',
        )
        _handle_discount_created(self._discount_obj('NVD-ZZZZZ'))
        self.new_user.refresh_from_db()
        self.assertEqual(self.new_user.referred_by, self.referrer)  # unchanged

    def test_unknown_code_is_silent_noop(self):
        _handle_discount_created(self._discount_obj('NVD-UNKNOWN'))
        self.new_user.refresh_from_db()
        self.assertIsNone(self.new_user.referred_by)


class ReferralDiscountComputation(BillingTestCase):
    """
    compute_discount counts active referred users at 12.5% each.
    Verifies the int() truncation and edge cases.
    """

    def setUp(self):
        super().setUp()
        self.referrer = make_user('ref_disc_r')
        cust = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust.id)
        Subscription.objects.create(
            user=self.referrer, stripe_customer_id=cust.id,
            status='active', referral_code='NVD-DISC1',
        )

    def _make_referred(self, username, status):
        u = make_user(username)
        cust = create_stripe_customer(u.email, u.username)
        self.track('customer', cust.id)
        Subscription.objects.create(user=u, stripe_customer_id=cust.id, status=status)
        u.referred_by = self.referrer
        u.save()
        return u

    def test_no_referrals_zero_discount(self):
        self.assertEqual(compute_discount(self.referrer), 0)

    def test_one_active_referral_gives_12_percent(self):
        # 12.5 truncated to int = 12
        self._make_referred('ref_d_a1', 'active')
        self.assertEqual(compute_discount(self.referrer), 12)

    def test_two_active_referrals_gives_25_percent(self):
        # 2 * 12.5 = 25.0 -> int = 25
        self._make_referred('ref_d_a2', 'active')
        self._make_referred('ref_d_a3', 'active')
        self.assertEqual(compute_discount(self.referrer), 25)

    def test_trialing_referred_does_not_count(self):
        self._make_referred('ref_d_t1', 'trialing')
        self.assertEqual(compute_discount(self.referrer), 0)

    def test_cancelled_referred_does_not_count(self):
        self._make_referred('ref_d_c1', 'cancelled')
        self.assertEqual(compute_discount(self.referrer), 0)

    def test_eight_active_referrals_caps_at_100(self):
        # 8 * 12.5 = 100 — exactly at cap
        for i in range(8):
            self._make_referred(f'ref_d_cap{i}', 'active')
        self.assertEqual(compute_discount(self.referrer), 100)

    def test_nine_active_referrals_still_100(self):
        for i in range(9):
            self._make_referred(f'ref_d_over{i}', 'active')
        self.assertEqual(compute_discount(self.referrer), 100)
