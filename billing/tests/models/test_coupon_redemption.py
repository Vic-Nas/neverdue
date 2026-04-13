# billing/tests/models/test_coupon_redemption.py
import uuid

from django.db import IntegrityError
from django.test import TestCase

from billing.models import Coupon, CouponRedemption
from billing.tests.helpers import make_user


def _coupon(code=None):
        return Coupon.objects.create(
            code=code or f'RDM{uuid.uuid4().hex[:5].upper()}',
            percent='10.00',
        )


class TestCouponRedemptionConstraints(TestCase):

    def test_unique_coupon_user(self):
        """Duplicate (coupon, user) raises IntegrityError."""
        coupon = _coupon()
        user = make_user(f'u_{uuid.uuid4().hex[:6]}')
        CouponRedemption.objects.create(coupon=coupon, user=user)
        with self.assertRaises(IntegrityError):
            CouponRedemption.objects.create(coupon=coupon, user=user)

    def test_multiple_coupons_same_user(self):
        """User can redeem two distinct coupons."""
        user = make_user(f'u_{uuid.uuid4().hex[:6]}')
        c1, c2 = _coupon(), _coupon()
        CouponRedemption.objects.create(coupon=c1, user=user)
        CouponRedemption.objects.create(coupon=c2, user=user)
        self.assertEqual(CouponRedemption.objects.filter(user=user).count(), 2)

    def test_delete_on_cascade_coupon(self):
        """Deleting Coupon cascades to its redemption."""
        coupon = _coupon()
        user = make_user(f'u_{uuid.uuid4().hex[:6]}')
        redemption = CouponRedemption.objects.create(coupon=coupon, user=user)
        coupon.delete()
        self.assertFalse(CouponRedemption.objects.filter(pk=redemption.pk).exists())

    def test_delete_on_cascade_user(self):
        """Deleting User cascades to their redemptions."""
        coupon = _coupon()
        user = make_user(f'u_{uuid.uuid4().hex[:6]}')
        redemption = CouponRedemption.objects.create(coupon=coupon, user=user)
        user.delete()
        self.assertFalse(CouponRedemption.objects.filter(pk=redemption.pk).exists())
