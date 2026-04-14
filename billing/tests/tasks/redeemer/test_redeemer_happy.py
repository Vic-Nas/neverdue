# billing/tests/tasks/redeemer/test_redeemer_happy.py
from django.test import tag
from django.utils import timezone

from billing.models import RefundRecord
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    BillingTestCase, last_month_start, make_coupon, make_djstripe_invoice,
    make_redemption, make_subscription, make_user,
)


@tag('stripe')
class RedeemerHappyTest(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

    def test_head_paid_redeemer_paid_creates_refund_record(self):
        head = make_user('hd_happy1')
        redeemer = make_user('rd_happy1')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_h1')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_rd_h1')
        coupon = make_coupon(head=head, code='HH0001', percent='12.50')
        make_redemption(coupon, redeemer)
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_h1')
        make_djstripe_invoice(redeemer, 800, self.lm, charge_id='ch_rd_h1')

        process_monthly_refunds(timestamp=self.now_ts)

        rr = RefundRecord.objects.get(redemption__user=redeemer)
        self.assertIsNotNone(rr.stripe_refund_id)
        self.assertGreater(rr.amount, 0)

    def test_head_none_redeemer_paid_no_head_check(self):
        redeemer = make_user('rd_happy2')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_rd_h2')
        coupon = make_coupon(head=None, code='HH0002', percent='12.50')
        make_redemption(coupon, redeemer)
        make_djstripe_invoice(redeemer, 800, self.lm, charge_id='ch_rd_h2')

        process_monthly_refunds(timestamp=self.now_ts)

        self.assertEqual(RefundRecord.objects.filter(redemption__user=redeemer).count(), 1)
