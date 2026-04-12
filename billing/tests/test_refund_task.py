# billing/tests/test_refund_task.py
"""
Tests for process_monthly_refunds Procrastinate task.

djstripe Invoice rows are seeded directly via make_djstripe_invoice().
stripe.Refund.create is mocked — no live Stripe charge needed.

Run with:
  python manage.py test billing.tests.test_refund_task \
      --settings=billing.tests.settings_test
"""
from unittest.mock import MagicMock, patch

import stripe as real_stripe
from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord, Subscription, UserCoupon
from billing.tests.helpers import make_admin_sentinel, make_djstripe_invoice, make_user


def _make_sub(user, status='active', stripe_customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=stripe_customer_id or f'cus_{user.username}',
        stripe_subscription_id=f'sub_{user.username}',
        status=status,
    )


def _make_coupon(percent, *users):
    c = UserCoupon.objects.create(percent=str(percent))
    c.users.set(users)
    return c


def _last_month_start():
    now = timezone.now()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first - timezone.timedelta(days=1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


class MonthlyRefundTask(TestCase):

    def setUp(self):
        super().setUp()
        self.last_month = _last_month_start()
        self.admin = make_admin_sentinel()

    def _run_task(self):
        from billing.tasks import process_monthly_refunds
        process_monthly_refunds(int(timezone.now().timestamp()))

    def _make_paying_user(self, username, amount_cents=800, charge_id=None):
        user = make_user(username)
        _make_sub(user, status='active', stripe_customer_id=f'cus_{username}')
        inv = make_djstripe_invoice(
            user, amount_cents, self.last_month,
            charge_id=charge_id or f'ch_{username}',
        )
        return user, inv

    def _backdate_coupon(self, coupon, days_before=5):
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=days_before)
        )

    # --- happy path ---

    @patch('billing.tasks.stripe')
    def test_both_paid_creates_two_refund_records(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_both')
        a, _ = self._make_paying_user('rt_a1', charge_id='ch_a1')
        b, _ = self._make_paying_user('rt_b1', charge_id='ch_b1')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)
        self.assertEqual(mock_stripe.Refund.create.call_count, 2)

    @patch('billing.tasks.stripe')
    def test_refund_amount_is_percent_of_invoice(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_amt')
        a, _ = self._make_paying_user('rt_amt_a', amount_cents=800, charge_id='ch_amt_a')
        b, _ = self._make_paying_user('rt_amt_b', amount_cents=800, charge_id='ch_amt_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        self._run_task()

        for c in mock_stripe.Refund.create.call_args_list:
            self.assertEqual(c.kwargs['amount'], 100)  # ceil(12.5% of 800) = 100

    @patch('billing.tasks.stripe')
    def test_partial_invoice_amount_correct(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_part')
        a, _ = self._make_paying_user('rt_pa_a', amount_cents=400, charge_id='ch_pa_a')
        b, _ = self._make_paying_user('rt_pa_b', amount_cents=400, charge_id='ch_pa_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        self._run_task()

        for c in mock_stripe.Refund.create.call_args_list:
            self.assertEqual(c.kwargs['amount'], 50)  # ceil(12.5% of 400) = 50

    @patch('billing.tasks.stripe')
    def test_refund_targets_correct_charge(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_chg')
        a, _ = self._make_paying_user('rt_chg_a', charge_id='ch_chg_a')
        b, _ = self._make_paying_user('rt_chg_b', charge_id='ch_chg_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        self._run_task()

        charges_used = {c.kwargs['charge'] for c in mock_stripe.Refund.create.call_args_list}
        self.assertIn('ch_chg_a', charges_used)
        self.assertIn('ch_chg_b', charges_used)

    # --- admin sentinel ---

    @patch('billing.tasks.stripe')
    def test_admin_sentinel_never_receives_refund(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_adm')
        user, _ = self._make_paying_user('rt_adm_u', charge_id='ch_adm_u')
        coupon = _make_coupon('20.00', user, self.admin)
        self._backdate_coupon(coupon)

        self._run_task()

        # Only one refund: for the real paying user
        self.assertEqual(mock_stripe.Refund.create.call_count, 1)
        self.assertEqual(
            mock_stripe.Refund.create.call_args.kwargs['charge'], 'ch_adm_u'
        )
        # Admin is not checked for invoice — task must not error
        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 1)

    # --- skips ---

    @patch('billing.tasks.stripe')
    def test_one_unpaid_skips_entire_coupon(self, mock_stripe):
        a, _ = self._make_paying_user('rt_sk_a', charge_id='ch_sk_a')
        b = make_user('rt_sk_b')
        _make_sub(b, status='cancelled', stripe_customer_id='cus_rt_sk_b')
        # b has no invoice
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 0)
        mock_stripe.Refund.create.assert_not_called()

    @patch('billing.tasks.stripe')
    def test_coupon_created_after_invoice_skipped(self, mock_stripe):
        a, _ = self._make_paying_user('rt_dt_a', charge_id='ch_dt_a')
        b, _ = self._make_paying_user('rt_dt_b', charge_id='ch_dt_b')
        coupon = _make_coupon('12.50', a, b)
        # Coupon created AFTER last month's invoice
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month + timezone.timedelta(days=20)
        )

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 0)
        mock_stripe.Refund.create.assert_not_called()

    @patch('billing.tasks.stripe')
    def test_coupon_predates_invoice_refund_issued(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_pre')
        a, _ = self._make_paying_user('rt_pre_a', charge_id='ch_pre_a')
        b, _ = self._make_paying_user('rt_pre_b', charge_id='ch_pre_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon, days_before=5)

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)

    # --- idempotency ---

    @patch('billing.tasks.stripe')
    def test_existing_record_skips_stripe_call(self, mock_stripe):
        a, inv_a = self._make_paying_user('rt_id_a', charge_id='ch_id_a')
        b, inv_b = self._make_paying_user('rt_id_b', charge_id='ch_id_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)
        RefundRecord.objects.create(
            user_coupon=coupon, stripe_invoice_id=inv_a.id,
            stripe_refund_id='re_pre_a', amount=100,
        )
        RefundRecord.objects.create(
            user_coupon=coupon, stripe_invoice_id=inv_b.id,
            stripe_refund_id='re_pre_b', amount=100,
        )

        self._run_task()

        mock_stripe.Refund.create.assert_not_called()

    @patch('billing.tasks.stripe')
    def test_full_run_is_idempotent(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_idem')
        a, _ = self._make_paying_user('rt_idem_a', charge_id='ch_idem_a')
        b, _ = self._make_paying_user('rt_idem_b', charge_id='ch_idem_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        self._run_task()
        first_call_count = mock_stripe.Refund.create.call_count

        self._run_task()
        self.assertEqual(mock_stripe.Refund.create.call_count, first_call_count)
        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)

    # --- error handling ---

    @patch('billing.tasks.stripe')
    def test_stripe_error_writes_no_record_and_raises(self, mock_stripe):
        mock_stripe.Refund.create.side_effect = real_stripe.error.StripeError('fail')
        a, _ = self._make_paying_user('rt_err_a', charge_id='ch_err_a')
        b, _ = self._make_paying_user('rt_err_b', charge_id='ch_err_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        with self.assertRaises(RuntimeError):
            self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 0)

    @patch('billing.tasks.stripe')
    def test_retry_after_stripe_error_succeeds(self, mock_stripe):
        mock_stripe.Refund.create.side_effect = [
            real_stripe.error.StripeError('fail'),
            MagicMock(id='re_retry_a'),
            MagicMock(id='re_retry_b'),
        ]
        a, _ = self._make_paying_user('rt_ret_a', charge_id='ch_ret_a')
        b, _ = self._make_paying_user('rt_ret_b', charge_id='ch_ret_b')
        coupon = _make_coupon('12.50', a, b)
        self._backdate_coupon(coupon)

        with self.assertRaises(RuntimeError):
            self._run_task()

        self._run_task()  # retry succeeds

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)

    # --- multiple coupons ---

    @patch('billing.tasks.stripe')
    def test_user_on_two_coupons_gets_two_refunds(self, mock_stripe):
        mock_stripe.Refund.create.side_effect = [
            MagicMock(id='re_t1'), MagicMock(id='re_t2'),
            MagicMock(id='re_t3'), MagicMock(id='re_t4'),
        ]
        a, _ = self._make_paying_user('rt_two_a', charge_id='ch_two_a')
        b, _ = self._make_paying_user('rt_two_b', charge_id='ch_two_b')
        c, _ = self._make_paying_user('rt_two_c', charge_id='ch_two_c')
        coupon1 = _make_coupon('12.50', a, b)
        coupon2 = _make_coupon('12.50', a, c)
        for cp in [coupon1, coupon2]:
            self._backdate_coupon(cp)

        self._run_task()

        # a: 2 refunds (one per coupon), b and c: 1 each
        self.assertEqual(mock_stripe.Refund.create.call_count, 4)