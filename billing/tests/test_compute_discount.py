# billing/tests/test_compute_discount.py
"""
Unit tests for compute_discount() and DB-level model constraints.
No Stripe API calls — DB only.

compute_discount uses math.ceil so a single 12.5% referral returns 13,
two referrals return 25 (ceil(25.0)), etc.

Run with:
  python manage.py test billing.tests.test_compute_discount \
      --settings=billing.tests.settings_test
"""
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from billing.models import RefundRecord, Subscription, UserCoupon, compute_discount
from billing.tests.helpers import make_admin_sentinel, make_user


def _make_sub(user, status='active', stripe_customer_id=None, stripe_subscription_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=stripe_customer_id or f'cus_{user.username}',
        stripe_subscription_id=stripe_subscription_id or f'sub_{user.username}',
        status=status,
    )


def _make_coupon(percent, *users):
    c = UserCoupon.objects.create(percent=str(percent))
    c.users.set(users)
    return c


# ---------------------------------------------------------------------------
# compute_discount — status rules
# ---------------------------------------------------------------------------

class ComputeDiscountStatusRules(TestCase):

    def test_no_coupons_returns_zero(self):
        user = make_user('cd_none')
        self.assertEqual(compute_discount(user), 0)

    def test_other_active_counts(self):
        a, b = make_user('cd_a1'), make_user('cd_b1')
        _make_sub(b, status='active')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 13)  # ceil(12.5)

    def test_other_cancelled_does_not_count(self):
        a, b = make_user('cd_a2'), make_user('cd_b2')
        _make_sub(b, status='cancelled')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)

    def test_other_trialing_does_not_count(self):
        # Trialing means they haven't paid yet
        a, b = make_user('cd_a3'), make_user('cd_b3')
        _make_sub(b, status='trialing')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)

    def test_other_past_due_does_not_count(self):
        a, b = make_user('cd_a_pd'), make_user('cd_b_pd')
        _make_sub(b, status='past_due')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)

    def test_admin_sentinel_always_counts(self):
        admin = make_admin_sentinel()
        user = make_user('cd_staff1')
        _make_coupon('20.00', user, admin)
        self.assertEqual(compute_discount(user), 20)


# ---------------------------------------------------------------------------
# compute_discount — arithmetic and stacking
# ---------------------------------------------------------------------------

class ComputeDiscountArithmetic(TestCase):

    def test_two_active_partners_sums(self):
        # ceil(12.5 + 12.5) = ceil(25.0) = 25
        a, b, c = make_user('cd_a4'), make_user('cd_b4'), make_user('cd_c4')
        _make_sub(b, status='active')
        _make_sub(c, status='active')
        _make_coupon('12.50', a, b)
        _make_coupon('12.50', a, c)
        self.assertEqual(compute_discount(a), 25)

    def test_one_active_one_cancelled_partial(self):
        # Only one coupon active: ceil(12.5) = 13
        a, b, c = make_user('cd_a5'), make_user('cd_b5'), make_user('cd_c5')
        _make_sub(b, status='active')
        _make_sub(c, status='cancelled')
        _make_coupon('12.50', a, b)
        _make_coupon('12.50', a, c)
        self.assertEqual(compute_discount(a), 13)

    def test_staff_plus_referral_stacks(self):
        # ceil(20.0 + 12.5) = ceil(32.5) = 33
        admin = make_admin_sentinel()
        a, b = make_user('cd_a6'), make_user('cd_b6')
        _make_sub(b, status='active')
        _make_coupon('20.00', a, admin)
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 33)

    def test_capped_at_100(self):
        admin = make_admin_sentinel()
        a = make_user('cd_a7')
        for i in range(10):
            u = make_user(f'cd_cap{i}')
            _make_sub(u, status='active')
            _make_coupon('12.50', a, u)
        _make_coupon('20.00', a, admin)
        self.assertEqual(compute_discount(a), 100)

    def test_ceiling_on_fractional_percent(self):
        # ceil(12.5) = 13, not 12. Returns int.
        a, b = make_user('cd_ceil_a'), make_user('cd_ceil_b')
        _make_sub(b, status='active')
        _make_coupon('12.50', a, b)
        result = compute_discount(a)
        self.assertEqual(result, 13)
        self.assertIsInstance(result, int)

    def test_two_referrals_do_not_double_ceil(self):
        # ceil(25.0) = 25, not 26. Ceiling is applied once to the sum, not per coupon.
        a, b, c = make_user('cd_dc_a'), make_user('cd_dc_b'), make_user('cd_dc_c')
        _make_sub(b, status='active')
        _make_sub(c, status='active')
        _make_coupon('12.50', a, b)
        _make_coupon('12.50', a, c)
        self.assertEqual(compute_discount(a), 25)


# ---------------------------------------------------------------------------
# compute_discount — state transitions
# ---------------------------------------------------------------------------

class ComputeDiscountStateTransitions(TestCase):

    def test_other_cancels_discount_drops(self):
        a, b = make_user('cd_a8'), make_user('cd_b8')
        sub_b = _make_sub(b, status='active')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 13)
        sub_b.status = 'cancelled'
        sub_b.save()
        self.assertEqual(compute_discount(a), 0)

    def test_other_resubscribes_discount_restores(self):
        a, b = make_user('cd_a9'), make_user('cd_b9')
        sub_b = _make_sub(b, status='cancelled')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)
        sub_b.status = 'active'
        sub_b.save()
        self.assertEqual(compute_discount(a), 13)

    def test_referrer_cancels_new_user_loses_discount(self):
        # Symmetric: A cancels, B loses discount
        a, b = make_user('cd_ref_can_a'), make_user('cd_ref_can_b')
        sub_a = _make_sub(a, status='active')
        _make_sub(b, status='active')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(b), 13)
        sub_a.status = 'cancelled'
        sub_a.save()
        self.assertEqual(compute_discount(b), 0)


# ---------------------------------------------------------------------------
# compute_discount — isolation
# ---------------------------------------------------------------------------

class ComputeDiscountIsolation(TestCase):

    def test_chain_no_cross_contamination(self):
        """B has coupon with A and coupon with C. A and C share nothing."""
        a, b, c = make_user('cd_chA'), make_user('cd_chB'), make_user('cd_chC')
        _make_sub(a, status='active')
        _make_sub(b, status='active')
        _make_sub(c, status='active')
        _make_coupon('12.50', b, a)
        _make_coupon('12.50', b, c)

        self.assertEqual(compute_discount(b), 25)  # ceil(25.0)
        self.assertEqual(compute_discount(a), 13)  # ceil(12.5)
        self.assertEqual(compute_discount(c), 13)  # ceil(12.5)
        self.assertFalse(
            UserCoupon.objects.filter(users=a).filter(users=c).exists()
        )

    def test_multiple_referrals_from_same_referrer(self):
        """A refers B and C independently — A earns from both, B and C don't share."""
        a = make_user('cd_multi_a')
        b, c = make_user('cd_multi_b'), make_user('cd_multi_c')
        _make_sub(a, status='active')
        _make_sub(b, status='active')
        _make_sub(c, status='active')
        _make_coupon('12.50', a, b)
        _make_coupon('12.50', a, c)

        self.assertEqual(compute_discount(a), 25)  # ceil(25.0) — earns from both
        self.assertEqual(compute_discount(b), 13)  # ceil(12.5) — only linked to a
        self.assertEqual(compute_discount(c), 13)  # ceil(12.5) — only linked to a
        self.assertFalse(
            UserCoupon.objects.filter(users=b).filter(users=c).exists()
        )


# ---------------------------------------------------------------------------
# DB constraints
# ---------------------------------------------------------------------------

class UserCouponIntegrity(TestCase):

    def test_refund_record_unique_together_blocks_double(self):
        user = make_user('rr_u1')
        coupon = _make_coupon('12.50', user)
        RefundRecord.objects.create(
            user_coupon=coupon,
            stripe_invoice_id='in_test_001',
            stripe_refund_id='re_test_001',
            amount=100,
        )
        with self.assertRaises(IntegrityError):
            RefundRecord.objects.create(
                user_coupon=coupon,
                stripe_invoice_id='in_test_001',
                stripe_refund_id='re_test_002',
                amount=100,
            )

    def test_refund_record_protect_on_coupon_delete(self):
        user = make_user('rr_u2')
        coupon = _make_coupon('12.50', user)
        RefundRecord.objects.create(
            user_coupon=coupon,
            stripe_invoice_id='in_test_002',
            stripe_refund_id='re_test_003',
            amount=100,
        )
        with self.assertRaises(ProtectedError):
            coupon.delete()

    def test_subscription_referral_code_unique(self):
        a, b = make_user('rr_u3'), make_user('rr_u4')
        _make_sub(a, stripe_customer_id='cus_rr3').referral_code
        sub_a = Subscription.objects.get(user=a)
        sub_a.referral_code = 'NVD-UNIQU'
        sub_a.save()
        sub_b = _make_sub(b, stripe_customer_id='cus_rr4')
        sub_b.referral_code = 'NVD-UNIQU'
        with self.assertRaises(Exception):  # IntegrityError or ValidationError
            sub_b.save()
