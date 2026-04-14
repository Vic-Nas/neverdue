# billing/tests/tasks/redeemer/test_redeemer_skips.py
from unittest.mock import patch

import stripe
from django.test import TestCase
from django.utils import timezone

from billing.models import CouponRedemption, RefundRecord
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    last_month_start, make_coupon, make_djstripe_invoice,
    make_redemption, make_subscription, make_user,
)


class RedeemerSkipsTest(TestCase):

    def setUp(self):
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

    def test_head_did_not_pay_redeemer_skipped(self):
        head = make_user('hd_skip1')
        redeemer = make_user('rd_skip1')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_sk1')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_rd_sk1')
        coupon = make_coupon(head=head, code='SK0001')
        make_redemption(coupon, redeemer)
        make_djstripe_invoice(redeemer, 800, self.lm, charge_id='ch_rd_sk1')
        # head has no invoice

        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.count(), 0)

    def test_redeemer_did_not_pay_skipped(self):
        head = make_user('hd_skip2')
        redeemer = make_user('rd_skip2')
        make_subscription(head, status='active', stripe_customer_id='cus_hd_sk2')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_rd_sk2')
        coupon = make_coupon(head=head, code='SK0002')
        make_redemption(coupon, redeemer)
        make_djstripe_invoice(head, 800, self.lm, charge_id='ch_hd_sk2')
        # redeemer has no invoice

        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.count(), 0)

    def test_future_dated_redemption_skipped(self):
        redeemer = make_user('rd_skip3')
        make_subscription(redeemer, status='active', stripe_customer_id='cus_rd_sk3')
        coupon = make_coupon(head=None, code='SK0003')
        redemption = make_redemption(coupon, redeemer)
        future = timezone.now() + timezone.timedelta(days=10)
        CouponRedemption.objects.filter(pk=redemption.pk).update(redeemed_at=future)
        make_djstripe_invoice(redeemer, 800, self.lm, charge_id='ch_rd_sk3')

        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.count(), 0)

    def test_stripe_error_raises_runtime_error_other_runs_unaffected(self):
        # Redemption with a bogus charge triggers RuntimeError via StripeError.
        # We simulate the StripeError via mock so we don't hit real Stripe.
        redeemer1 = make_user('rd_skip4a')
        make_subscription(redeemer1, status='active', stripe_customer_id='cus_rd_sk4a')
        coupon1 = make_coupon(head=None, code='SK004A')
        make_redemption(coupon1, redeemer1)
        inv = make_djstripe_invoice(redeemer1, 800, self.lm, charge_id='ch_bogus_zzz')
        inv.stripe_data = {**inv.stripe_data, 'charge': 'ch_bogus_zzz'}
        inv.save()

        def _raise_stripe_error(charge, amount):
            raise stripe.error.InvalidRequestError(
                message=f'No such charge: {charge!r}',
                param='charge',
            )

        with self.assertRaises(RuntimeError):
            with patch('billing.tasks.stripe.Refund.create', side_effect=_raise_stripe_error):
                process_monthly_refunds(timestamp=self.now_ts)

        # Second independent redemption has no invoice → cleanly skipped on fresh run
        redeemer2 = make_user('rd_skip4b')
        make_subscription(redeemer2, status='active', stripe_customer_id='cus_rd_sk4b')
        coupon2 = make_coupon(head=None, code='SK004B')
        make_redemption(coupon2, redeemer2)
        process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.count(), 0)
