# billing/tests/tasks/head/test_head_happy.py
import math
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    BillingTestCase, last_month_start, make_coupon, make_djstripe_invoice,
    make_redemption, make_subscription, make_user,
)


def _fake_refund(charge, amount):
    r = MagicMock()
    r.id = f're_fake_{charge}'
    return r


class HeadHappyTest(TestCase):

    def setUp(self):
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

    def test_one_redeemer_paid_head_gets_refund(self):
        head = make_user('hd_hh1')
        rd = make_user('rd_hh1')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_hh1')
        make_subscription(rd, status='active', stripe_customer_id='cus_rd_hh1')
        coupon = make_coupon(head=head, code='HH_H01', percent='12.50')
        make_redemption(coupon, rd)
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_hh1')
        make_djstripe_invoice(rd, 800, self.lm, charge_id='ch_rd_hh1')

        with patch('billing.tasks.stripe.Refund.create', side_effect=_fake_refund):
            process_monthly_refunds(timestamp=self.now_ts)

        rr = RefundRecord.objects.get(coupon_head=coupon)
        self.assertEqual(rr.amount, math.ceil(800 * 12.5 / 100))

    def test_three_redeemers_paid_head_gets_triple(self):
        head = make_user('hd_hh2')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_hh2')
        coupon = make_coupon(head=head, code='HH_H02', percent='12.50')
        for i in range(3):
            rd = make_user(f'rd_hh2_{i}')
            make_subscription(rd, status='active', stripe_customer_id=f'cus_rd_hh2_{i}')
            make_redemption(coupon, rd)
            make_djstripe_invoice(rd, 800, self.lm, charge_id=f'ch_rd_hh2_{i}')
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_hh2')

        with patch('billing.tasks.stripe.Refund.create', side_effect=_fake_refund):
            process_monthly_refunds(timestamp=self.now_ts)

        rr = RefundRecord.objects.get(coupon_head=coupon)
        self.assertEqual(rr.amount, math.ceil(800 * 37.5 / 100))

    def test_effective_percent_over_100_capped(self):
        head = make_user('hd_hh3')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_hh3')
        coupon = make_coupon(head=head, code='HH_H03', percent='12.50')
        for i in range(10):
            rd = make_user(f'rd_hh3_{i}')
            make_subscription(rd, status='active', stripe_customer_id=f'cus_rd_hh3_{i}')
            make_redemption(coupon, rd)
            make_djstripe_invoice(rd, 800, self.lm, charge_id=f'ch_rd_hh3_{i}')
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_hh3')

        with patch('billing.tasks.stripe.Refund.create', side_effect=_fake_refund):
            process_monthly_refunds(timestamp=self.now_ts)

        rr = RefundRecord.objects.get(coupon_head=coupon)
        self.assertEqual(rr.amount, 800)  # 100% capped
