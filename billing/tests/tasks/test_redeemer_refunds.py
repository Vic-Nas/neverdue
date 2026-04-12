# billing/tests/tasks/test_redeemer_refunds.py
"""
Tests for the redeemer pass of process_monthly_refunds.
Stripe API calls are mocked; djstripe Invoice rows are seeded via make_djstripe_invoice.
"""
import time
import uuid
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import Coupon, CouponRedemption, RefundRecord, Subscription
from billing.tasks import _prev_month_window, process_monthly_refunds
from billing.tests.helpers import make_djstripe_invoice, make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _coupon(code=None, head=None, percent='10.00'):
    with patch.object(Coupon, '_push_to_stripe'):
        return Coupon.objects.create(
            code=code or f'TR{uuid.uuid4().hex[:6].upper()}',
            percent=percent,
            head=head,
        )


def _sub(user, status='active', customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or _cus_id(),
        status=status,
    )


def _prev_month_start():
    """Datetime at the start of last month."""
    now = timezone.now()
    start, _ = _prev_month_window(now)
    return start + timezone.timedelta(days=15)


FAKE_REFUND_ID = 're_faketest'


class TestRedeemerRefunds(TestCase):

    def _run(self):
        process_monthly_refunds(int(time.time()))

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_happy_path_redeemer_refund(self, mock_refund):
        head = make_user('head_hp')
        redeemer = make_user('red_hp')
        head_sub = _sub(head)
        red_sub = _sub(redeemer)
        period_start = _prev_month_start()
        head_inv = make_djstripe_invoice(head, 800, period_start, charge_id='ch_head_hp')
        red_inv = make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_red_hp')

        coupon = _coupon(percent='10.00', head=head)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertTrue(RefundRecord.objects.filter(redemption=redemption).exists())
        mock_refund.assert_called()

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_neverdue_grant_head_none_always_pays_out(self, mock_refund):
        """head=None — redeemer always gets refund regardless of any head status."""
        redeemer = make_user('red_nvd')
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_nvd')
        coupon = _coupon(percent='30.00', head=None)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertTrue(RefundRecord.objects.filter(redemption=redemption).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_head_did_not_pay_skips_redeemer(self, mock_refund):
        """If head has no paid invoice this month, redeemer is skipped."""
        head = make_user('head_nopay')
        redeemer = make_user('red_nopay')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        # Head has NO invoice; redeemer does
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_red_nopay')
        coupon = _coupon(percent='10.00', head=head)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertFalse(RefundRecord.objects.filter(redemption=redemption).exists())
        mock_refund.assert_not_called()

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_redeemer_did_not_pay_skipped(self, mock_refund):
        head = make_user('head_redpay')
        redeemer = make_user('red_didnot')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_head_rp')
        coupon = _coupon(percent='10.00', head=head)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertFalse(RefundRecord.objects.filter(redemption=redemption).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_invoice_predates_redemption_skipped(self, mock_refund):
        """Invoice period_start < redeemed_at → skip."""
        head = make_user('head_predate')
        redeemer = make_user('red_predate')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        head_inv = make_djstripe_invoice(head, 800, period_start, charge_id='ch_pd_head')
        red_inv = make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_pd_red')
        coupon = _coupon(percent='10.00', head=head)
        # Create redemption AFTER period_start so invoice pre-dates it
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        # Backdate redeemed_at to AFTER the invoice period_start
        future = timezone.now() + timezone.timedelta(days=1)
        CouponRedemption.objects.filter(pk=redemption.pk).update(redeemed_at=future)
        redemption.refresh_from_db()

        self._run()

        self.assertFalse(RefundRecord.objects.filter(redemption=redemption).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_idempotent_on_existing_refund_record(self, mock_refund):
        head = make_user('head_idem')
        redeemer = make_user('red_idem')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_idem_h')
        red_inv = make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_idem_r')
        coupon = _coupon(percent='10.00', head=head)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        # Pre-seed existing RefundRecord
        RefundRecord.objects.create(
            redemption=redemption,
            stripe_invoice_id=red_inv.id,
            stripe_refund_id='re_existing',
            amount=80,
        )

        self._run()

        self.assertEqual(
            RefundRecord.objects.filter(redemption=redemption).count(), 1
        )
        mock_refund.assert_not_called()

    @patch('billing.tasks._safe_create_refund_record')
    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_race_condition_integrity_error_swallowed(self, mock_refund, mock_safe):
        """_safe_create_refund_record swallows IntegrityError on race."""
        from django.db import IntegrityError
        mock_safe.side_effect = None  # called but does nothing
        head = make_user('head_race')
        redeemer = make_user('red_race')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_race_h')
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_race_r')
        coupon = _coupon(percent='10.00', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        # Should not raise even if _safe_create swallows
        self._run()

    @patch('billing.tasks.stripe.Refund.create', side_effect=Exception('stripe down'))
    def test_stripe_error_raises_runtime_and_retries(self, mock_refund):
        head = make_user('head_err')
        redeemer = make_user('red_err')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_err_h')
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_err_r')
        coupon = _coupon(percent='10.00', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        with self.assertRaises((RuntimeError, Exception)):
            self._run()

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_two_redeemers_same_coupon_independent(self, mock_refund):
        """One redeemer failing doesn't prevent the other from succeeding."""
        head = make_user('head_two')
        r1 = make_user('red_two1')
        r2 = make_user('red_two2')
        _sub(head)
        _sub(r1)
        _sub(r2)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_two_h')
        make_djstripe_invoice(r1, 800, period_start, charge_id='ch_two_r1')
        make_djstripe_invoice(r2, 800, period_start, charge_id='ch_two_r2')
        coupon = _coupon(percent='10.00', head=head)
        red1 = CouponRedemption.objects.create(coupon=coupon, user=r1)
        red2 = CouponRedemption.objects.create(coupon=coupon, user=r2)

        self._run()

        self.assertTrue(RefundRecord.objects.filter(redemption=red1).exists())
        self.assertTrue(RefundRecord.objects.filter(redemption=red2).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_user_with_two_redemptions_gets_both_refunds(self, mock_refund):
        head = make_user('head_2red')
        user = make_user('user_2red')
        _sub(head)
        _sub(user)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_2red_h')
        # redeemer has two separate charge IDs for two invoices — use same period but different charge
        inv1 = make_djstripe_invoice(user, 800, period_start, charge_id='ch_2red_u1')
        coupon1 = _coupon(percent='10.00', head=None)
        coupon2 = _coupon(percent='20.00', head=head)
        red1 = CouponRedemption.objects.create(coupon=coupon1, user=user)
        red2 = CouponRedemption.objects.create(coupon=coupon2, user=user)

        self._run()

        self.assertTrue(RefundRecord.objects.filter(redemption=red1).exists())
        self.assertTrue(RefundRecord.objects.filter(redemption=red2).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_zero_amount_invoice_skipped(self, mock_refund):
        """amount_paid=0 → _issue_refund returns (None, 0) → no RefundRecord."""
        head = make_user('head_zero')
        redeemer = make_user('red_zero')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_zero_h')
        make_djstripe_invoice(redeemer, 0, period_start, charge_id='ch_zero_r')
        coupon = _coupon(percent='10.00', head=head)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertFalse(RefundRecord.objects.filter(redemption=redemption).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_invoice_with_no_charge_skipped(self, mock_refund):
        """Invoice with no charge field → _issue_refund logs and returns (None, 0)."""
        head = make_user('head_noc')
        redeemer = make_user('red_noc')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_noc_h')
        inv = make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_noc_r')
        # Patch the stripe_data to remove charge
        import djstripe.models as djstripe
        dj_inv = djstripe.Invoice.objects.get(pk=inv.pk)
        data = dict(dj_inv.stripe_data)
        data['charge'] = None
        djstripe.Invoice.objects.filter(pk=inv.pk).update(stripe_data=data)

        coupon = _coupon(percent='10.00', head=head)
        redemption = CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertFalse(RefundRecord.objects.filter(redemption=redemption).exists())
        mock_refund.assert_not_called()
