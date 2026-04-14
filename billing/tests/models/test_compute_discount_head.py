# billing/tests/models/test_compute_discount_head.py
from django.test import TestCase

from billing.models import compute_discount
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


class ComputeDiscountHeadTest(TestCase):

    def setUp(self):
        self.head = make_user('headuser')
        make_subscription(self.head, status='active')
        self.coupon = make_coupon(head=self.head, code='HEAD01', percent='12.50')

    def test_no_redeemers_zero(self):
        self.assertEqual(compute_discount(self.head), 0)

    def test_two_active_redeemers(self):
        for i in range(2):
            u = make_user(f'rd{i}')
            make_subscription(u, status='active')
            make_redemption(self.coupon, u)
        self.assertEqual(compute_discount(self.head), 25)  # ceil(12.5 * 2)

    def test_trialing_redeemer_not_counted(self):
        u = make_user('trial_rd')
        make_subscription(u, status='trialing')
        make_redemption(self.coupon, u)
        # head-side uses literal status == 'active'; trialing is excluded
        self.assertEqual(compute_discount(self.head), 0)

    def test_capped_at_100(self):
        for i in range(10):
            u = make_user(f'cap_rd{i}')
            make_subscription(u, status='active')
            make_redemption(self.coupon, u)
        # 12.5 * 10 = 125 → capped at 100
        self.assertEqual(compute_discount(self.head), 100)
