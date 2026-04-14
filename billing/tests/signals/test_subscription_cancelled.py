# billing/tests/signals/test_subscription_cancelled.py
from django.test import TestCase

from billing.models import Coupon, CouponRedemption
from billing.signals import handle_subscription_cancelled
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


def _event(customer_id):
    return {'data': {'object': {'customer': customer_id}}}


class SubscriptionCancelledTest(TestCase):

    def setUp(self):
        self.user = make_user('canceller')
        make_subscription(self.user, status='active', stripe_customer_id='cus_can1')

    def test_two_redemptions_deleted(self):
        c1 = make_coupon(code='DEL001')
        c2 = make_coupon(code='DEL002')
        make_redemption(c1, self.user)
        make_redemption(c2, self.user)
        handle_subscription_cancelled(_event('cus_can1'))
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_zero_redemptions_no_error(self):
        handle_subscription_cancelled(_event('cus_can1'))

    def test_no_local_subscription_silent(self):
        handle_subscription_cancelled(_event('cus_nobody'))

    def test_coupon_not_deleted(self):
        coupon = make_coupon(code='KEEP01', head=self.user)
        handle_subscription_cancelled(_event('cus_can1'))
        self.assertTrue(Coupon.objects.filter(pk=coupon.pk).exists())
