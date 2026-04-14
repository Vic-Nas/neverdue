# billing/tests/tasks/helpers/test_issue_refund.py
from unittest.mock import MagicMock, patch

import stripe
from django.test import tag

from billing.tasks import _issue_refund
from billing.tests.helpers import (
    BillingTestCase, last_month_start, make_djstripe_invoice, make_subscription, make_user,
)


@tag('stripe')
class IssueRefundTest(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('refunduser')
        make_subscription(self.user, stripe_customer_id='cus_rftest1')
        self.lm = last_month_start()

    def test_normal_refund(self):
        inv = make_djstripe_invoice(self.user, 800, self.lm, charge_id='ch_rftest1')
        fake_refund = MagicMock()
        fake_refund.id = 're_fake_rftest1'
        with patch('billing.tasks.stripe.Refund.create', return_value=fake_refund) as mock_create:
            refund_id, amount = _issue_refund(inv, 12.5, 'test')
            mock_create.assert_called_once_with(charge='ch_rftest1', amount=100)
        self.assertEqual(refund_id, 're_fake_rftest1')
        self.assertEqual(amount, 100)  # ceil(800 * 12.5 / 100) = 100

    def test_amount_paid_zero_returns_none(self):
        inv = make_djstripe_invoice(self.user, 0, self.lm, charge_id='ch_rftest2')
        refund_id, amount = _issue_refund(inv, 12.5, 'test')
        self.assertIsNone(refund_id)
        self.assertEqual(amount, 0)

    def test_no_charge_field_returns_none(self):
        inv = make_djstripe_invoice(self.user, 800, self.lm, charge_id='ch_rftest3')
        inv.stripe_data = {k: v for k, v in inv.stripe_data.items() if k != 'charge'}
        refund_id, amount = _issue_refund(inv, 12.5, 'test')
        self.assertIsNone(refund_id)
        self.assertEqual(amount, 0)

    def test_stripe_error_propagates(self):
        inv = make_djstripe_invoice(self.user, 800, self.lm, charge_id='ch_bogus_zzz')
        def _raise(*args, **kwargs):
            raise stripe.error.InvalidRequestError(
                message="No such charge: 'ch_bogus_zzz'",
                param='charge',
            )
        with patch('billing.tasks.stripe.Refund.create', side_effect=_raise):
            with self.assertRaises(stripe.error.StripeError):
                _issue_refund(inv, 12.5, 'test')