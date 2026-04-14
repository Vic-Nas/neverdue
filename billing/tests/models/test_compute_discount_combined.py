# billing/tests/models/test_compute_discount_combined.py
from django.test import TestCase

from billing.models import compute_discount
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


class ComputeDiscountCombinedTest(TestCase):

    def test_user_is_both_head_and_redeemer(self):
        user = make_user('combo')
        sponsor = make_user('sponsor')
        make_subscription(user, status='active')
        make_subscription(sponsor, status='active')
        sponsor_coupon = make_coupon(head=sponsor, code='SPONS1', percent='10.00')
        make_redemption(sponsor_coupon, user)
        rd = make_user('rd1')
        make_subscription(rd, status='active')
        own_coupon = make_coupon(head=user, code='OWN001', percent='12.50')
        make_redemption(own_coupon, rd)
        # redeemer side: 10; head side: 12.5 → total ceil(22.5) = 23
        self.assertEqual(compute_discount(user), 23)

    def test_user_with_no_subscription_no_redemptions(self):
        user = make_user('nobody')
        self.assertEqual(compute_discount(user), 0)
