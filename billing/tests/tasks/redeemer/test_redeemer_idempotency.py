# billing/tests/tasks/redeemer/test_redeemer_idempotency.py
from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    last_month_start, make_coupon, make_djstripe_invoice,
    make_redemption, make_subscription, make_user,
)


class RedeemerIdempotencyTest(TestCase):

    def setUp(self):
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

    def _setup_with_existing_record(self, suffix):
        redeemer = make_user(f'rd_idem_{suffix}')
        make_subscription(redeemer, status='active',
                          stripe_customer_id=f'cus_rd_id_{suffix}')
        coupon = make_coupon(head=None, code=f'IDEM{suffix}')
        redemption = make_redemption(coupon, redeemer)
        inv = make_djstripe_invoice(redeemer, 800, self.lm,
                                    charge_id=f'ch_rd_id_{suffix}')
        RefundRecord.objects.create(
            redemption=redemption,
            stripe_invoice_id=inv.id,
            stripe_refund_id='re_prior',
            amount=100,
        )
        return redemption

    def test_existing_refund_record_no_second_refund(self):
        redemption = self._setup_with_existing_record('A1')
        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(redemption=redemption).count(), 1)

    def test_running_twice_one_record_per_redemption(self):
        redemption = self._setup_with_existing_record('A2')
        process_monthly_refunds(timestamp=self.now_ts)
        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(redemption=redemption).count(), 1)
