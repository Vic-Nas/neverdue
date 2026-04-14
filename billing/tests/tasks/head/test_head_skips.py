# billing/tests/tasks/head/test_head_skips.py
from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    last_month_start, make_coupon, make_djstripe_invoice,
    make_redemption, make_subscription, make_user,
)


class HeadSkipsTest(TestCase):

    def setUp(self):
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

    def test_head_did_not_pay_no_head_refund_record(self):
        head = make_user('hd_sk1')
        rd = make_user('rd_sk1')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_sk1')
        make_subscription(rd, status='active', stripe_customer_id='cus_rd_sk1')
        coupon = make_coupon(head=head, code='HSK001')
        make_redemption(coupon, rd)
        make_djstripe_invoice(rd, 800, self.lm, charge_id='ch_rd_sk1')
        # head has no invoice

        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(coupon_head=coupon).count(), 0)

    def test_head_paid_zero_redeemers_no_head_refund_record(self):
        head = make_user('hd_sk2')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_sk2')
        coupon = make_coupon(head=head, code='HSK002')
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_sk2')
        # no redeemers at all

        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(coupon_head=coupon).count(), 0)

    def test_future_dated_redemption_excluded_from_count(self):
        head = make_user('hd_sk3')
        rd = make_user('rd_sk3')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_sk3')
        make_subscription(rd, status='active', stripe_customer_id='cus_rd_sk3')
        coupon = make_coupon(head=head, code='HSK003')
        redemption = make_redemption(coupon, rd)
        future = timezone.now() + timezone.timedelta(days=10)
        from billing.models import CouponRedemption
        CouponRedemption.objects.filter(pk=redemption.pk).update(redeemed_at=future)
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_sk3')
        make_djstripe_invoice(rd, 800, self.lm, charge_id='ch_rd_sk3')

        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(coupon_head=coupon).count(), 0)
