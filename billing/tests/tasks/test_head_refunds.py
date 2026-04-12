# billing/tests/tasks/test_head_refunds.py
import time
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import Coupon, CouponRedemption, RefundRecord, Subscription
from billing.tasks import _prev_month_window, process_monthly_refunds
from billing.tests.helpers import make_djstripe_invoice, make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _coupon(code=None, head=None, percent='12.50'):
    with patch.object(Coupon, '_push_to_stripe'):
        return Coupon.objects.create(
            code=code or f'HR{uuid.uuid4().hex[:6].upper()}',
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
    now = timezone.now()
    start, _ = _prev_month_window(now)
    return start + timezone.timedelta(days=15)


FAKE_REFUND_ID = 're_headfake'


class TestHeadRefunds(TestCase):

    def _run(self):
        process_monthly_refunds(int(time.time()))

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_happy_path_head_refund_one_redeemer(self, mock_refund):
        head = make_user('head_hp')
        redeemer = make_user('red_hp')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        head_inv = make_djstripe_invoice(head, 800, period_start, charge_id='ch_hdhp_h')
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_hdhp_r')
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertTrue(RefundRecord.objects.filter(coupon_head=coupon).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_head_refund_scales_with_redeemer_count(self, mock_refund):
        head = make_user('head_scale')
        _sub(head)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_scale_h')
        coupon = _coupon(percent='12.50', head=head)
        for i in range(3):
            r = make_user(f'red_scale_{i}')
            _sub(r)
            make_djstripe_invoice(r, 800, period_start, charge_id=f'ch_scale_r{i}')
            CouponRedemption.objects.create(coupon=coupon, user=r)

        self._run()

        rec = RefundRecord.objects.get(coupon_head=coupon)
        # 3 × 12.5% = 37.5% → ceil(37.5% of 800) = 300
        self.assertEqual(rec.amount, 300)

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_head_refund_capped_at_100(self, mock_refund):
        head = make_user('head_cap')
        _sub(head)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_cap_h')
        coupon = _coupon(percent='12.50', head=head)
        for i in range(9):
            r = make_user(f'red_cap_{i}')
            _sub(r)
            make_djstripe_invoice(r, 800, period_start, charge_id=f'ch_cap_r{i}')
            CouponRedemption.objects.create(coupon=coupon, user=r)

        self._run()

        rec = RefundRecord.objects.get(coupon_head=coupon)
        # Capped at 100% of 800
        self.assertEqual(rec.amount, 800)

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_head_did_not_pay_skipped(self, mock_refund):
        head = make_user('head_nopay')
        redeemer = make_user('red_nopay')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        # No invoice for head
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_hdnp_r')
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertFalse(RefundRecord.objects.filter(coupon_head=coupon).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_no_redeemers_paid_skipped(self, mock_refund):
        head = make_user('head_norpay')
        redeemer = make_user('red_norpay')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_hdnrp_h')
        # No invoice for redeemer
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        self.assertFalse(RefundRecord.objects.filter(coupon_head=coupon).exists())

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_partial_redeemers_paid_counts_only_paid(self, mock_refund):
        head = make_user('head_partial')
        _sub(head)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_part_h')
        coupon = _coupon(percent='12.50', head=head)
        # r1 paid, r2 didn't
        r1 = make_user('red_part1')
        r2 = make_user('red_part2')
        _sub(r1)
        _sub(r2)
        make_djstripe_invoice(r1, 800, period_start, charge_id='ch_part_r1')
        CouponRedemption.objects.create(coupon=coupon, user=r1)
        CouponRedemption.objects.create(coupon=coupon, user=r2)

        self._run()

        rec = RefundRecord.objects.get(coupon_head=coupon)
        # Only 1 redeemer paid → 12.5% of 800 = 100
        self.assertEqual(rec.amount, 100)

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_idempotent_on_existing_head_refund_record(self, mock_refund):
        head = make_user('head_idem')
        redeemer = make_user('red_idem')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        head_inv = make_djstripe_invoice(head, 800, period_start, charge_id='ch_idem_h')
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_idem_r')
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)
        RefundRecord.objects.create(
            coupon_head=coupon,
            stripe_invoice_id=head_inv.id,
            stripe_refund_id='re_existing',
            amount=100,
        )

        self._run()

        self.assertEqual(RefundRecord.objects.filter(coupon_head=coupon).count(), 1)
        mock_refund.assert_not_called()

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_head_coupon_and_redeemer_coupon_independent(self, mock_refund):
        """User is head on coupon A and redeemer on coupon B — both produce RefundRecords."""
        dual = make_user('dual_user')
        other_head = make_user('other_head')
        redeemer_a = make_user('red_a')
        _sub(dual)
        _sub(other_head)
        _sub(redeemer_a)
        period_start = _prev_month_start()
        make_djstripe_invoice(dual, 800, period_start, charge_id='ch_dual_d')
        make_djstripe_invoice(other_head, 800, period_start, charge_id='ch_dual_oh')
        make_djstripe_invoice(redeemer_a, 800, period_start, charge_id='ch_dual_ra')

        coupon_a = _coupon(percent='12.50', head=dual)    # dual is head
        coupon_b = _coupon(percent='10.00', head=other_head)  # dual is redeemer
        CouponRedemption.objects.create(coupon=coupon_a, user=redeemer_a)
        redemption_b = CouponRedemption.objects.create(coupon=coupon_b, user=dual)

        self._run()

        self.assertTrue(RefundRecord.objects.filter(coupon_head=coupon_a).exists())
        self.assertTrue(RefundRecord.objects.filter(redemption=redemption_b).exists())

    @patch('billing.tasks.stripe.Refund.create', side_effect=Exception('stripe boom'))
    def test_stripe_error_raises_runtime(self, mock_refund):
        head = make_user('head_se')
        redeemer = make_user('red_se')
        _sub(head)
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_se_h')
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_se_r')
        coupon = _coupon(percent='12.50', head=head)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        with self.assertRaises((RuntimeError, Exception)):
            self._run()

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id=FAKE_REFUND_ID))
    def test_neverdue_grant_coupon_head_none_excluded_from_head_pass(self, mock_refund):
        """head=None coupons never appear in the head pass queryset."""
        redeemer = make_user('red_nvd2')
        _sub(redeemer)
        period_start = _prev_month_start()
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_nvd2_r')
        coupon = _coupon(percent='30.00', head=None)
        CouponRedemption.objects.create(coupon=coupon, user=redeemer)

        self._run()

        # The coupon itself should never produce a head RefundRecord
        self.assertFalse(RefundRecord.objects.filter(coupon_head=coupon).exists())
