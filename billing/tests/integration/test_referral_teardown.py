# billing/tests/integration/test_referral_teardown.py
from django.test import TestCase
from django.urls import reverse

from billing.models import CouponRedemption, compute_discount
from billing.signals import handle_checkout_session_completed, handle_subscription_cancelled
from billing.tests.helpers import make_subscription, make_user


class ReferralTeardownTest(TestCase):

    def setUp(self):
        self.head = make_user('td_head1')
        self.redeemer = make_user('td_rd1')
        self.head_sub = make_subscription(self.head, status='active',
                                          stripe_customer_id='cus_td_h1')
        make_subscription(self.redeemer, status='active',
                          stripe_customer_id='cus_td_rd1')
        code = self.head_sub.generate_referral_code()
        event = {'data': {'object': {
            'customer': 'cus_td_rd1',
            'metadata': {'coupon_code': code},
        }}}
        handle_checkout_session_completed(event)
        self.assertEqual(CouponRedemption.objects.count(), 1)

    def test_cancellation_deletes_redemption(self):
        event = {'data': {'object': {'customer': 'cus_td_rd1'}}}
        handle_subscription_cancelled(event)
        self.assertEqual(CouponRedemption.objects.count(), 0)

    def test_compute_discount_head_zero_after_cancellation(self):
        event = {'data': {'object': {'customer': 'cus_td_rd1'}}}
        handle_subscription_cancelled(event)
        self.assertEqual(compute_discount(self.head), 0)

    def test_plans_view_active_partners_zero_after_cancellation(self):
        event = {'data': {'object': {'customer': 'cus_td_rd1'}}}
        handle_subscription_cancelled(event)
        self.client.force_login(self.head)
        r = self.client.get(reverse('billing:membership'))
        self.assertEqual(r.context['active_partners'], 0)
