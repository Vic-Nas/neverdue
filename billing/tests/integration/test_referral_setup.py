# billing/tests/integration/test_referral_setup.py
from django.test import TestCase

from billing.models import Coupon, CouponRedemption, compute_discount
from billing.signals import handle_checkout_session_completed
from billing.tests.helpers import make_subscription, make_user


class ReferralSetupTest(TestCase):

    def setUp(self):
        self.head = make_user('int_head1')
        self.sub = make_subscription(self.head, status='active',
                                     stripe_customer_id='cus_int_h1')

    def test_generate_referral_code_creates_coupon_with_head(self):
        code = self.sub.generate_referral_code()
        coupon = Coupon.objects.get(code=code)
        self.assertEqual(coupon.head, self.head)

    def test_checkout_signal_creates_redemption(self):
        code = self.sub.generate_referral_code()
        redeemer = make_user('int_rd1')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_int_rd1')
        event = {'data': {'object': {
            'customer': 'cus_int_rd1',
            'metadata': {'coupon_code': code},
        }}}
        handle_checkout_session_completed(event)
        self.assertEqual(CouponRedemption.objects.filter(user=redeemer).count(), 1)

    def test_self_referral_blocked(self):
        code = self.sub.generate_referral_code()
        event = {'data': {'object': {
            'customer': 'cus_int_h1',
            'metadata': {'coupon_code': code},
        }}}
        handle_checkout_session_completed(event)
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_compute_discount_before_payment(self):
        code = self.sub.generate_referral_code()
        redeemer = make_user('int_rd2')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_int_rd2')
        event = {'data': {'object': {
            'customer': 'cus_int_rd2',
            'metadata': {'coupon_code': code},
        }}}
        handle_checkout_session_completed(event)
        # head side: 1 active redeemer → ceil(12.5) = 13
        self.assertEqual(compute_discount(self.head), 13)
        # redeemer side: head is active → ceil(12.5) = 13
        self.assertEqual(compute_discount(redeemer), 13)
