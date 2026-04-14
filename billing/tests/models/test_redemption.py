# billing/tests/models/test_redemption.py
from django.db import IntegrityError
from django.test import TestCase

from billing.models import CouponRedemption
from billing.tests.helpers import make_coupon, make_user


class RedemptionModelTest(TestCase):

    def setUp(self):
        self.user = make_user('redeemer1')
        self.coupon = make_coupon(code='RED001')

    def test_create_and_str(self):
        r = CouponRedemption.objects.create(coupon=self.coupon, user=self.user)
        self.assertEqual(str(r), 'redeemer1 → RED001')

    def test_unique_together_raises(self):
        CouponRedemption.objects.create(coupon=self.coupon, user=self.user)
        with self.assertRaises(IntegrityError):
            CouponRedemption.objects.create(coupon=self.coupon, user=self.user)

    def test_same_user_two_coupons_allowed(self):
        coupon2 = make_coupon(code='RED002')
        CouponRedemption.objects.create(coupon=self.coupon, user=self.user)
        CouponRedemption.objects.create(coupon=coupon2, user=self.user)
        self.assertEqual(CouponRedemption.objects.filter(user=self.user).count(), 2)
