# billing/tests/tasks/head/test_head_idempotency.py
from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    last_month_start, make_coupon, make_djstripe_invoice,
    make_redemption, make_subscription, make_user,
)


class HeadIdempotencyTest(TestCase):

    def setUp(self):
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

    def _setup(self, suffix):
        head = make_user(f'hd_idem_{suffix}')
        rd = make_user(f'rd_idem_{suffix}')
        make_subscription(head, status='active', stripe_customer_id=f'cus_hd_id_{suffix}')
        make_subscription(rd, status='active', stripe_customer_id=f'cus_rd_id_{suffix}')
        coupon = make_coupon(head=head, code=f'HIDM{suffix}')
        make_redemption(coupon, rd)
        head_inv = make_djstripe_invoice(head, 800, self.lm, charge_id=f'ch_hd_id_{suffix}')
        make_djstripe_invoice(rd, 800, self.lm, charge_id=f'ch_rd_id_{suffix}')
        return coupon, head_inv

    def test_existing_head_record_no_second_refund(self):
        coupon, head_inv = self._setup('B1')
        RefundRecord.objects.create(
            coupon_head=coupon,
            stripe_invoice_id=head_inv.id,
            stripe_refund_id='re_head_existing',
            amount=100,
        )
        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(coupon_head=coupon).count(), 1)

    def test_running_twice_one_record_for_head(self):
        coupon, head_inv = self._setup('B2')
        RefundRecord.objects.create(
            coupon_head=coupon,
            stripe_invoice_id=head_inv.id,
            stripe_refund_id='re_head_first',
            amount=100,
        )
        process_monthly_refunds(timestamp=self.now_ts)
        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.filter(coupon_head=coupon).count(), 1)
