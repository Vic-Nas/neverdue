# billing/tests/models/test_coupon.py
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from billing.models import Coupon
from billing.tests.helpers import make_coupon, make_user


class CouponModelTest(TestCase):

    def test_str_format(self):
        coupon = make_coupon(code='SAVE10', percent='12.50')
        self.assertEqual(str(coupon), 'SAVE10 (12.50%)')

    def test_head_none_is_valid(self):
        coupon = make_coupon(head=None, code='GRANT1')
        self.assertIsNone(coupon.head)
        self.assertEqual(Coupon.objects.filter(pk=coupon.pk).count(), 1)

    def test_code_uniqueness_raises(self):
        make_coupon(code='DUPE01')
        with self.assertRaises(IntegrityError):
            make_coupon(code='DUPE01')

    def test_max_redemptions_none_allowed(self):
        coupon = make_coupon(code='UNLIM1', max_redemptions=None)
        self.assertIsNone(coupon.max_redemptions)
        self.assertEqual(Coupon.objects.filter(pk=coupon.pk).count(), 1)
