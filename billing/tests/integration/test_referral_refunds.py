# billing/tests/integration/test_referral_refunds.py
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord
from billing.signals import handle_checkout_session_completed
from billing.tasks import process_monthly_refunds
from billing.tests.helpers import (
    last_month_start, make_djstripe_invoice, make_subscription, make_user,
)


def _fake_refund(charge, amount):
    r = MagicMock()
    r.id = f're_fake_{charge}'
    return r


class ReferralRefundsTest(TestCase):

    def setUp(self):
        self.lm = last_month_start()
        self.now_ts = int(timezone.now().timestamp())

        self.head = make_user('rf_head1')
        self.redeemer = make_user('rf_rd1')
        self.head_sub = make_subscription(self.head, status='active',
                                          stripe_customer_id='cus_rf_h1')
        make_subscription(self.redeemer, status='active',
                          stripe_customer_id='cus_rf_rd1')

        code = self.head_sub.generate_referral_code()
        event = {'data': {'object': {
            'customer': 'cus_rf_rd1',
            'metadata': {'coupon_code': code},
        }}}
        handle_checkout_session_completed(event)

        make_djstripe_invoice(self.head, 800, self.lm, charge_id='ch_rf_h1')
        make_djstripe_invoice(self.redeemer, 800, self.lm, charge_id='ch_rf_rd1')

    def test_both_paid_creates_two_refund_records(self):
        with patch('billing.tasks.stripe.Refund.create', side_effect=_fake_refund):
            process_monthly_refunds(timestamp=self.now_ts)
        redeemer_rr = RefundRecord.objects.filter(redemption__user=self.redeemer)
        head_rr = RefundRecord.objects.filter(coupon_head__head=self.head)
        self.assertEqual(redeemer_rr.count(), 1)
        self.assertEqual(head_rr.count(), 1)

    def test_running_twice_fully_idempotent(self):
        with patch('billing.tasks.stripe.Refund.create', side_effect=_fake_refund):
            process_monthly_refunds(timestamp=self.now_ts)
            count_after_first = RefundRecord.objects.count()
            process_monthly_refunds(timestamp=self.now_ts)
        self.assertEqual(RefundRecord.objects.count(), count_after_first)
