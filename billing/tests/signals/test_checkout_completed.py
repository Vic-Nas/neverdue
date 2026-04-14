# billing/tests/signals/test_checkout_completed.py
from django.test import TestCase

from billing.models import CouponRedemption
from billing.signals import handle_checkout_session_completed
from billing.tests.helpers import make_coupon, make_subscription, make_user


def _event(customer_id='cus_x', coupon_code=None):
    metadata = {'coupon_code': coupon_code} if coupon_code else {}
    return {'data': {'object': {'customer': customer_id, 'metadata': metadata}}}


class CheckoutCompletedTest(TestCase):

    def setUp(self):
        self.head = make_user('head1')
        make_subscription(self.head, status='active')
        self.coupon = make_coupon(head=self.head, code='CHECK1')
        self.new_user = make_user('buyer')
        make_subscription(self.new_user, status='active', stripe_customer_id='cus_buyer')

    def test_no_coupon_code_no_redemption(self):
        handle_checkout_session_completed(_event(customer_id='cus_buyer'))
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_unknown_code_no_redemption(self):
        handle_checkout_session_completed(_event('cus_buyer', 'UNKNOWN'))
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_no_local_subscription_no_redemption(self):
        handle_checkout_session_completed(_event('cus_nobody', 'CHECK1'))
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_self_referral_blocked(self):
        make_coupon(head=self.new_user, code='SELF01')
        handle_checkout_session_completed(_event('cus_buyer', 'SELF01'))
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_duplicate_redemption_skipped(self):
        CouponRedemption.objects.create(coupon=self.coupon, user=self.new_user)
        handle_checkout_session_completed(_event('cus_buyer', 'CHECK1'))
        self.assertEqual(CouponRedemption.objects.count(), 1)

    def test_happy_path_creates_redemption(self):
        handle_checkout_session_completed(_event('cus_buyer', 'CHECK1'))
        self.assertEqual(CouponRedemption.objects.count(), 1)
        r = CouponRedemption.objects.get()
        self.assertEqual(r.coupon, self.coupon)
        self.assertEqual(r.user, self.new_user)
