# billing/tests/models/test_compute_discount.py
import uuid

from django.test import TestCase

from billing.models import Coupon, CouponRedemption, Subscription, compute_discount
from billing.tests.helpers import make_user


def _coupon(code=None, percent='10.00', head=None):
    return Coupon.objects.create(
        code=code or f'C{uuid.uuid4().hex[:7].upper()}',
        percent=percent,
        head=head,
    )


def _sub(user, status='active', customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or f'cus_{uuid.uuid4().hex[:10]}',
        status=status,
    )


def _user(prefix='u'):
    return make_user(f'{prefix}_{uuid.uuid4().hex[:6]}')


# ---------------------------------------------------------------------------
# Redeemer side
# ---------------------------------------------------------------------------

class TestComputeDiscountRedeemerSide(TestCase):

    def test_redeemer_gets_percent_when_head_active(self):
        head = _user('h')
        _sub(head, 'active')
        redeemer = _user('r')
        coupon = _coupon(percent='20.00', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        self.assertEqual(compute_discount(redeemer), 20)

    def test_redeemer_gets_percent_when_head_none(self):
        """NeverDue grant — head=None — always pays out."""
        redeemer = _user('r')
        coupon = _coupon(percent='30.00', head=None)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        self.assertEqual(compute_discount(redeemer), 30)

    def test_redeemer_gets_nothing_when_head_cancelled(self):
        head = _user('h')
        _sub(head, 'cancelled')
        redeemer = _user('r')
        coupon = _coupon(percent='20.00', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        self.assertEqual(compute_discount(redeemer), 0)

    def test_redeemer_gets_nothing_when_head_past_due(self):
        head = _user('h')
        _sub(head, 'past_due')
        redeemer = _user('r')
        coupon = _coupon(percent='20.00', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        self.assertEqual(compute_discount(redeemer), 0)

    def test_redeemer_stacks_two_coupons(self):
        """Two redeemed coupons sum correctly."""
        head = _user('h')
        _sub(head, 'active')
        redeemer = _user('r')
        c1 = _coupon(percent='10.00', head=head)
        c2 = _coupon(percent='15.00', head=None)
        CouponRedemption.objects.create(coupon=c1, user=redeemer)
        CouponRedemption.objects.create(coupon=c2, user=redeemer)
        self.assertEqual(compute_discount(redeemer), 25)


# ---------------------------------------------------------------------------
# Head side
# ---------------------------------------------------------------------------

class TestComputeDiscountHeadSide(TestCase):

    def test_head_gets_percent_per_active_redeemer(self):
        head = _user('h')
        r1, r2 = _user('r1'), _user('r2')
        _sub(r1, 'active')
        _sub(r2, 'active')
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=r1)
        CouponRedemption.objects.create(coupon=coupon, user=r2)
        self.assertEqual(compute_discount(head), 25)

    def test_head_gets_nothing_with_zero_active_redeemers(self):
        head = _user('h')
        coupon = _coupon(percent='12.50', head=head)
        self.assertEqual(compute_discount(head), 0)

    def test_head_skips_cancelled_redeemers(self):
        head = _user('h')
        r_active, r_cancelled = _user('ra'), _user('rc')
        _sub(r_active, 'active')
        _sub(r_cancelled, 'cancelled')
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=r_active)
        CouponRedemption.objects.create(coupon=coupon, user=r_cancelled)
        self.assertEqual(compute_discount(head), 13)  # ceil(12.5)

    def test_head_stacks_two_coupons_they_head(self):
        head = _user('h')
        r1, r2 = _user('r1'), _user('r2')
        _sub(r1, 'active')
        _sub(r2, 'active')
        c1 = _coupon(percent='12.50', head=head)
        c2 = _coupon(percent='10.00', head=head)
        CouponRedemption.objects.create(coupon=c1, user=r1)
        CouponRedemption.objects.create(coupon=c2, user=r2)
        self.assertEqual(compute_discount(head), 23)  # ceil(12.5 + 10.0)


# ---------------------------------------------------------------------------
# Combined + edge cases
# ---------------------------------------------------------------------------

class TestComputeDiscountEdgeCases(TestCase):

    def test_head_and_redeemer_roles_stack(self):
        """User is head on one coupon and redeemer on another — both add up."""
        dual_user = _user('d')
        head2 = _user('h2')
        _sub(head2, 'active')
        r1 = _user('r1')
        _sub(r1, 'active')

        c_as_head = _coupon(percent='10.00', head=dual_user)
        c_as_redeemer = _coupon(percent='20.00', head=head2)
        CouponRedemption.objects.create(coupon=c_as_head, user=r1)
        CouponRedemption.objects.create(coupon=c_as_redeemer, user=dual_user)
        self.assertEqual(compute_discount(dual_user), 30)

    def test_cap_at_100(self):
        head = _user('h')
        redeemers = [_user(f'r{i}') for i in range(9)]
        for r in redeemers:
            _sub(r, 'active')
        coupon = _coupon(percent='12.50', head=head)
        for r in redeemers:
            CouponRedemption.objects.create(coupon=coupon, user=r)
        result = compute_discount(head)
        self.assertEqual(result, 100)

    def test_ceiling_arithmetic(self):
        """1 referral at 12.5 → 13, 2 → 25, 8 → 100."""
        for n, expected in [(1, 13), (2, 25), (8, 100)]:
            head = _user(f'h{n}')
            coupon = _coupon(percent='12.50', head=head)
            for i in range(n):
                r = _user(f'r{n}_{i}')
                _sub(r, 'active')
                CouponRedemption.objects.create(coupon=coupon, user=r)
            self.assertEqual(compute_discount(head), expected, msg=f'n={n}')

    def test_no_coupons_returns_zero(self):
        user = _user('lone')
        self.assertEqual(compute_discount(user), 0)
