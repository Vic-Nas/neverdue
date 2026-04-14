# billing/tests/models/test_subscription.py
from decimal import Decimal

from django.test import TestCase

from billing.models import Coupon, Subscription
from billing.tests.helpers import make_subscription, make_user


class SubscriptionModelTest(TestCase):

    def setUp(self):
        self.user = make_user('subuser')
        self.sub = make_subscription(self.user)

    def test_is_pro_active_and_trialing(self):
        for status in ('active', 'trialing'):
            self.sub.status = status
            self.sub.save()
            self.assertTrue(self.sub.is_pro)
        for status in ('cancelled', 'past_due'):
            self.sub.status = status
            self.sub.save()
            self.assertFalse(self.sub.is_pro)

    def test_referral_code_none_before_generation(self):
        self.assertIsNone(self.sub.referral_code)

    def test_generate_referral_code_default(self):
        code = self.sub.generate_referral_code()
        self.assertRegex(code, r'^NVD-[A-Z0-9]{5}$')
        coupon = self.sub.referral_coupon
        self.assertEqual(coupon.head, self.user)
        self.assertEqual(coupon.percent, Decimal('12.50'))
        self.assertEqual(coupon.max_redemptions, 12)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.referral_code, code)

    def test_generate_referral_code_head_none(self):
        code = self.sub.generate_referral_code(head=None)
        self.assertRegex(code, r'^NVD-[A-Z0-9]{5}$')
        self.assertIsNone(self.sub.referral_coupon.head)

    def test_generate_referral_code_idempotent(self):
        code1 = self.sub.generate_referral_code()
        count_before = Coupon.objects.count()
        code2 = self.sub.generate_referral_code()
        self.assertEqual(code1, code2)
        self.assertEqual(Coupon.objects.count(), count_before)
