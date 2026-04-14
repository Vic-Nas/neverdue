# billing/tests/tasks/helpers/test_get_paid_invoice.py
from django.test import TestCase
from django.utils import timezone

from billing.tasks import _get_paid_invoice, _prev_month_window
from billing.tests.helpers import (
    last_month_start, make_djstripe_invoice, make_subscription, make_user,
)


class GetPaidInvoiceTest(TestCase):

    def setUp(self):
        self.user = make_user('invuser')
        make_subscription(self.user, stripe_customer_id='cus_inv1')
        self.lm = last_month_start()
        self.start, self.end = _prev_month_window(timezone.now())

    def test_paid_invoice_in_window_returned(self):
        make_djstripe_invoice(self.user, 800, self.lm, charge_id='ch_a1')
        inv = _get_paid_invoice(self.user, self.start, self.end)
        self.assertIsNotNone(inv)

    def test_invoice_outside_window_returns_none(self):
        future = self.end + timezone.timedelta(days=60)
        make_djstripe_invoice(self.user, 800, future, charge_id='ch_a2')
        inv = _get_paid_invoice(self.user, self.start, self.end)
        self.assertIsNone(inv)

    def test_unpaid_invoice_returns_none(self):
        import djstripe.models as djstripe
        make_djstripe_invoice(self.user, 800, self.lm, charge_id='ch_a3')
        djstripe.Invoice.objects.filter(customer__id='cus_inv1').update(
            stripe_data={
                'status': 'open',
                'period_start': int(self.lm.timestamp()),
                'amount_paid': 800,
                'charge': 'ch_a3',
            }
        )
        inv = _get_paid_invoice(self.user, self.start, self.end)
        self.assertIsNone(inv)

    def test_no_subscription_returns_none(self):
        user2 = make_user('nosubuser')
        self.assertIsNone(_get_paid_invoice(user2, self.start, self.end))

    def test_two_invoices_most_recent_returned(self):
        lm2 = self.lm + timezone.timedelta(days=5)
        make_djstripe_invoice(self.user, 800, self.lm, charge_id='ch_b1')
        make_djstripe_invoice(self.user, 900, lm2, charge_id='ch_b2')
        inv = _get_paid_invoice(self.user, self.start, self.end)
        self.assertIsNotNone(inv)
        self.assertEqual(inv.stripe_data['amount_paid'], 900)
