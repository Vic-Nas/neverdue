# billing/tests/models/test_compute_discount_redeemer.py
from django.test import TestCase

from billing.models import compute_discount
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


class ComputeDiscountRedeemerTest(TestCase):

    def test_head_none_always_pays(self):
        redeemer = make_user('r1')
        coupon = make_coupon(head=None, code='GRANT1', percent='15.00')
        make_redemption(coupon, redeemer)
        self.assertEqual(compute_discount(redeemer), 15)

    def test_active_head_pays(self):
        head = make_user('head1')
        redeemer = make_user('r2')
        make_subscription(head, status='active')
        coupon = make_coupon(head=head, code='ACT001', percent='12.50')
        make_redemption(coupon, redeemer)
        self.assertEqual(compute_discount(redeemer), 13)  # ceil(12.5)

    def test_trialing_head_pays(self):
        head = make_user('head2')
        redeemer = make_user('r3')
        make_subscription(head, status='trialing')
        coupon = make_coupon(head=head, code='TRI001', percent='10.00')
        make_redemption(coupon, redeemer)
        self.assertEqual(compute_discount(redeemer), 10)

    def test_cancelled_head_zero(self):
        head = make_user('head3')
        redeemer = make_user('r4')
        make_subscription(head, status='cancelled')
        coupon = make_coupon(head=head, code='CAN001', percent='12.50')
        make_redemption(coupon, redeemer)
        self.assertEqual(compute_discount(redeemer), 0)

    def test_two_redemptions_sum(self):
        head1 = make_user('head4')
        head2 = make_user('head5')
        redeemer = make_user('r5')
        make_subscription(head1, status='active')
        make_subscription(head2, status='active')
        c1 = make_coupon(head=head1, code='SUM001', percent='12.50')
        c2 = make_coupon(head=head2, code='SUM002', percent='10.00')
        make_redemption(c1, redeemer)
        make_redemption(c2, redeemer)
        self.assertEqual(compute_discount(redeemer), 23)  # ceil(22.5)
