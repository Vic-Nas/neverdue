# billing/tests/test_user_coupon.py
"""
Full test suite for the refactored billing system.

Test classes:
  ComputeDiscount         — unit, DB only, no Stripe
  UserCouponIntegrity     — DB constraint tests
  SignalHandlers          — signal functions called directly, Stripe mocked
  MonthlyRefundTask       — process_monthly_refunds, djstripe Invoice rows, Stripe mocked
  PushCombinedDiscount    — real Stripe test mode
  GenerateReferralCode    — unit + real Stripe test mode
  SubscriptionWorkflow    — dj-stripe signal integration, real Stripe test mode
  BillingWorkflow         — end-to-end, real Stripe test mode

Run with:
  python manage.py test billing.tests.test_user_coupon \
      --settings=billing.tests.settings_test
"""
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import stripe
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from billing.models import RefundRecord, Subscription, UserCoupon, compute_discount
from billing.tests.helpers import (
    BillingTestCase,
    create_stripe_customer,
    create_stripe_subscription,
    make_admin_sentinel,
    make_djstripe_invoice,
    make_user,
    s,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sub(user, status='active', stripe_customer_id=None, stripe_subscription_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=stripe_customer_id or f'cus_{user.username}',
        stripe_subscription_id=stripe_subscription_id or f'sub_{user.username}',
        status=status,
    )


def _make_coupon(percent, *users):
    c = UserCoupon.objects.create(percent=str(percent))
    c.users.set(users)
    return c


def _last_month_start():
    now = timezone.now()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = (first - timezone.timedelta(days=1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return last_month


# ---------------------------------------------------------------------------
# ComputeDiscount
# ---------------------------------------------------------------------------

class ComputeDiscount(TestCase):

    def test_no_coupons_returns_zero(self):
        user = make_user('u_none')
        self.assertEqual(compute_discount(user), 0)

    def test_single_coupon_other_active_returns_percent(self):
        a = make_user('u_a1')
        b = make_user('u_b1')
        _make_sub(b, status='active')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 12)

    def test_single_coupon_other_cancelled_returns_zero(self):
        a = make_user('u_a2')
        b = make_user('u_b2')
        _make_sub(b, status='cancelled')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)

    def test_single_coupon_other_trialing_returns_zero(self):
        # Trialing means they haven't paid yet — does not count
        a = make_user('u_a3')
        b = make_user('u_b3')
        _make_sub(b, status='trialing')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)

    def test_admin_sentinel_always_counts(self):
        admin = make_admin_sentinel()
        user = make_user('u_staff1')
        _make_coupon('20.00', user, admin)
        self.assertEqual(compute_discount(user), 20)

    def test_two_coupons_both_others_active_sums_percents(self):
        a = make_user('u_a4')
        b = make_user('u_b4')
        c = make_user('u_c4')
        _make_sub(b, status='active')
        _make_sub(c, status='active')
        _make_coupon('12.50', a, b)
        _make_coupon('12.50', a, c)
        self.assertEqual(compute_discount(a), 25)

    def test_two_coupons_one_other_cancelled_only_active_counts(self):
        a = make_user('u_a5')
        b = make_user('u_b5')
        c = make_user('u_c5')
        _make_sub(b, status='active')
        _make_sub(c, status='cancelled')
        _make_coupon('12.50', a, b)
        _make_coupon('12.50', a, c)
        self.assertEqual(compute_discount(a), 12)

    def test_stack_20_plus_12_5_returns_32(self):
        admin = make_admin_sentinel()
        a = make_user('u_a6')
        b = make_user('u_b6')
        _make_sub(b, status='active')
        _make_coupon('20.00', a, admin)
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 32)

    def test_cap_at_100(self):
        admin = make_admin_sentinel()
        a = make_user('u_a7')
        users = [make_user(f'u_cap{i}') for i in range(10)]
        for u in users:
            _make_sub(u, status='active')
            _make_coupon('12.50', a, u)
        _make_coupon('20.00', a, admin)
        self.assertEqual(compute_discount(a), 100)

    def test_other_cancels_discount_drops(self):
        a = make_user('u_a8')
        b = make_user('u_b8')
        sub_b = _make_sub(b, status='active')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 12)
        sub_b.status = 'cancelled'
        sub_b.save()
        self.assertEqual(compute_discount(a), 0)

    def test_other_resubscribes_discount_restores(self):
        a = make_user('u_a9')
        b = make_user('u_b9')
        sub_b = _make_sub(b, status='cancelled')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 0)
        sub_b.status = 'active'
        sub_b.save()
        self.assertEqual(compute_discount(a), 12)

    def test_chain_no_cross_contamination(self):
        """B has coupon with A and coupon with C. All active. No A↔C bleed."""
        a = make_user('u_chainA')
        b = make_user('u_chainB')
        c = make_user('u_chainC')
        _make_sub(a, status='active')
        _make_sub(b, status='active')
        _make_sub(c, status='active')
        _make_coupon('12.50', b, a)
        _make_coupon('12.50', b, c)

        self.assertEqual(compute_discount(b), 25)
        self.assertEqual(compute_discount(a), 12)
        self.assertEqual(compute_discount(c), 12)

        # A and C share no coupon — they get nothing from each other
        # Build a coupon between a and c explicitly to confirm isolation
        # (they shouldn't have one yet)
        self.assertFalse(
            UserCoupon.objects.filter(users=a).filter(users=c).exists()
        )


# ---------------------------------------------------------------------------
# UserCouponIntegrity
# ---------------------------------------------------------------------------

class UserCouponIntegrity(TestCase):

    def test_refund_record_unique_together_blocks_double(self):
        user = make_user('u_rr1')
        coupon = _make_coupon('12.50', user)
        RefundRecord.objects.create(
            user_coupon=coupon,
            stripe_invoice_id='in_test_001',
            stripe_refund_id='re_test_001',
            amount=100,
        )
        with self.assertRaises(IntegrityError):
            RefundRecord.objects.create(
                user_coupon=coupon,
                stripe_invoice_id='in_test_001',
                stripe_refund_id='re_test_002',
                amount=100,
            )

    def test_refund_record_protect_on_coupon_delete(self):
        from django.db.models import ProtectedError
        user = make_user('u_rr2')
        coupon = _make_coupon('12.50', user)
        RefundRecord.objects.create(
            user_coupon=coupon,
            stripe_invoice_id='in_test_002',
            stripe_refund_id='re_test_003',
            amount=100,
        )
        with self.assertRaises(ProtectedError):
            coupon.delete()


# ---------------------------------------------------------------------------
# SignalHandlers
# ---------------------------------------------------------------------------

def _make_discount_event(coupon_id, customer_id):
    """Minimal dj-stripe-style event object for customer.discount.created."""
    event = MagicMock()
    event.type = 'customer.discount.created'
    event.data = {
        'object': {
            'coupon': {'id': coupon_id},
            'customer': customer_id,
        }
    }
    return event


def _make_invoice_event(customer_id, billing_reason='subscription_cycle'):
    event = MagicMock()
    event.type = 'invoice.paid'
    event.data = {
        'object': {
            'customer': customer_id,
            'billing_reason': billing_reason,
        }
    }
    return event


def _make_upcoming_event(customer_id):
    event = MagicMock()
    event.type = 'invoice.upcoming'
    event.data = {'object': {'customer': customer_id}}
    return event


def _make_sub_updated_event(customer_id, old_status, new_status):
    event = MagicMock()
    event.type = 'customer.subscription.updated'
    event.data = {
        'object': {'customer': customer_id, 'status': new_status},
        'previous_attributes': {'status': old_status},
    }
    return event


class SignalHandlers(TestCase):

    def setUp(self):
        super().setUp()
        # Import here so test DB is ready
        from billing.signals import (
            handle_customer_discount_created,
            handle_invoice_paid,
            handle_invoice_upcoming,
            handle_subscription_updated,
        )
        self.handle_discount = handle_customer_discount_created
        self.handle_invoice_paid = handle_invoice_paid
        self.handle_upcoming = handle_invoice_upcoming
        self.handle_sub_updated = handle_subscription_updated

    # ── customer.discount.created ──

    def test_known_referral_code_creates_user_coupon(self):
        referrer = make_user('sh_ref1')
        new_user = make_user('sh_new1')
        _make_sub(referrer, stripe_customer_id='cus_ref1')
        sub_ref = Subscription.objects.get(user=referrer)
        sub_ref.referral_code = 'NVD-AAAAA'
        sub_ref.save()
        _make_sub(new_user, stripe_customer_id='cus_new1')

        event = _make_discount_event('NVD-AAAAA', 'cus_new1')
        with patch('billing.signals.stripe'):
            self.handle_discount(event)

        self.assertTrue(
            UserCoupon.objects.filter(users=referrer).filter(users=new_user).exists()
        )

    def test_self_referral_no_coupon_stripe_discount_deleted(self):
        user = make_user('sh_self1')
        _make_sub(user, stripe_customer_id='cus_self1')
        sub = Subscription.objects.get(user=user)
        sub.referral_code = 'NVD-BBBBB'
        sub.save()

        event = _make_discount_event('NVD-BBBBB', 'cus_self1')
        with patch('billing.signals.stripe') as mock_stripe:
            self.handle_discount(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())
        mock_stripe.Customer.delete_discount.assert_called_once_with('cus_self1')

    def test_unknown_code_no_coupon_no_error(self):
        user = make_user('sh_unk1')
        _make_sub(user, stripe_customer_id='cus_unk1')

        event = _make_discount_event('UNKNOWN-CODE', 'cus_unk1')
        # Should not raise, should not create any coupon
        with patch('billing.signals.stripe'):
            self.handle_discount(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())

    def test_duplicate_webhook_exactly_one_coupon(self):
        referrer = make_user('sh_ref2')
        new_user = make_user('sh_new2')
        _make_sub(referrer, stripe_customer_id='cus_ref2')
        sub_ref = Subscription.objects.get(user=referrer)
        sub_ref.referral_code = 'NVD-CCCCC'
        sub_ref.save()
        _make_sub(new_user, stripe_customer_id='cus_new2')

        event = _make_discount_event('NVD-CCCCC', 'cus_new2')
        with patch('billing.signals.stripe'):
            self.handle_discount(event)
            self.handle_discount(event)

        count = (
            UserCoupon.objects
            .filter(users=referrer)
            .filter(users=new_user)
            .count()
        )
        self.assertEqual(count, 1)

    # ── invoice.paid ──

    def test_handle_invoice_paid_subscription_create_pushes_all_coupon_partners(self):
        a = make_user('sh_a_ic')
        b = make_user('sh_b_ic')
        _make_sub(a, stripe_customer_id='cus_a_ic', stripe_subscription_id='sub_a_ic')
        _make_sub(b, stripe_customer_id='cus_b_ic', stripe_subscription_id='sub_b_ic')
        _make_coupon('12.50', a, b)

        event = _make_invoice_event('cus_a_ic', billing_reason='subscription_create')
        with patch('billing.signals._push_combined_discount') as mock_push:
            self.handle_invoice_paid(event)

        pushed_pks = {c.args[0].user.pk for c in mock_push.call_args_list}
        self.assertIn(a.pk, pushed_pks)
        self.assertIn(b.pk, pushed_pks)

    def test_handle_invoice_paid_subscription_cycle_pushes_payer_only(self):
        a = make_user('sh_a_cy')
        b = make_user('sh_b_cy')
        _make_sub(a, stripe_customer_id='cus_a_cy', stripe_subscription_id='sub_a_cy')
        _make_sub(b, stripe_customer_id='cus_b_cy', stripe_subscription_id='sub_b_cy')
        _make_coupon('12.50', a, b)

        event = _make_invoice_event('cus_a_cy', billing_reason='subscription_cycle')
        with patch('billing.signals._push_combined_discount') as mock_push:
            self.handle_invoice_paid(event)

        self.assertEqual(mock_push.call_count, 1)
        self.assertEqual(mock_push.call_args[0][0].user.pk, a.pk)

    def test_handle_invoice_paid_no_coupons_no_error(self):
        user = make_user('sh_nc_ip')
        _make_sub(user, stripe_customer_id='cus_nc_ip', stripe_subscription_id='sub_nc_ip')

        event = _make_invoice_event('cus_nc_ip', billing_reason='subscription_create')
        with patch('billing.signals._push_combined_discount') as mock_push:
            self.handle_invoice_paid(event)

        # push still called for the payer themselves
        self.assertEqual(mock_push.call_count, 1)

    # ── invoice.upcoming ──

    def test_handle_invoice_upcoming_pushes_correct_user(self):
        user = make_user('sh_up1')
        _make_sub(user, stripe_customer_id='cus_up1', stripe_subscription_id='sub_up1',
                  status='active')

        event = _make_upcoming_event('cus_up1')
        with patch('billing.signals._push_combined_discount') as mock_push:
            self.handle_upcoming(event)

        self.assertEqual(mock_push.call_count, 1)
        self.assertEqual(mock_push.call_args[0][0].user.pk, user.pk)

    def test_handle_invoice_upcoming_skips_non_pro(self):
        user = make_user('sh_up2')
        _make_sub(user, stripe_customer_id='cus_up2', status='cancelled')

        event = _make_upcoming_event('cus_up2')
        with patch('billing.signals._push_combined_discount') as mock_push:
            self.handle_upcoming(event)

        mock_push.assert_not_called()

    # ── customer.subscription.updated ──

    def test_handle_subscription_updated_any_to_active_defers_retry(self):
        user = make_user('sh_su1')
        _make_sub(user, stripe_customer_id='cus_su1', status='trialing')

        event = _make_sub_updated_event('cus_su1', 'trialing', 'active')
        with patch('billing.signals.retry_jobs_after_plan_upgrade') as mock_retry:
            # Need to patch at the point of import inside the function
            with patch('emails.tasks.retry_jobs_after_plan_upgrade') as mock_retry2:
                self.handle_sub_updated(event)
                mock_retry2.defer.assert_called_once_with(user_id=user.pk)

    def test_handle_subscription_updated_active_to_active_no_retry(self):
        user = make_user('sh_su2')
        _make_sub(user, stripe_customer_id='cus_su2', status='active')

        event = _make_sub_updated_event('cus_su2', 'active', 'active')
        with patch('emails.tasks.retry_jobs_after_plan_upgrade') as mock_retry:
            self.handle_sub_updated(event)
            mock_retry.defer.assert_not_called()

    def test_handle_subscription_updated_no_local_sub_no_error(self):
        event = _make_sub_updated_event('cus_nonexistent', 'trialing', 'active')
        # Should log a warning but not raise
        self.handle_sub_updated(event)


# ---------------------------------------------------------------------------
# MonthlyRefundTask
# ---------------------------------------------------------------------------

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
        _make_sub(user, status='active',
                  stripe_customer_id=f'cus_{username}',
                  stripe_subscription_id=f'sub_{username}')
        inv = make_djstripe_invoice(
            user, amount_cents, self.last_month,
            charge_id=charge_id or f'ch_{username}',
        )
        return user, inv

    @patch('billing.tasks.stripe')
    def test_both_paid_refunds_both_creates_records(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_test_both')
        a, inv_a = self._make_paying_user('mrt_a1', charge_id='ch_a1')
        b, inv_b = self._make_paying_user('mrt_b1', charge_id='ch_b1')
        coupon = _make_coupon('12.50', a, b)

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)
        self.assertEqual(mock_stripe.Refund.create.call_count, 2)

    @patch('billing.tasks.stripe')
    def test_one_user_no_invoice_no_refund_for_that_user(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_test_one')
        a, inv_a = self._make_paying_user('mrt_a2', charge_id='ch_a2')
        b = make_user('mrt_b2')
        _make_sub(b, status='cancelled', stripe_customer_id='cus_mrt_b2')
        # b has no invoice
        coupon = _make_coupon('12.50', a, b)

        self._run_task()

        # b didn't pay → nobody on this coupon gets a refund
        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 0)
        mock_stripe.Refund.create.assert_not_called()

    @patch('billing.tasks.stripe')
    def test_coupon_created_after_invoice_date_skipped(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_skip')
        a, _ = self._make_paying_user('mrt_a3', charge_id='ch_a3')
        b, _ = self._make_paying_user('mrt_b3', charge_id='ch_b3')
        coupon = _make_coupon('12.50', a, b)
        # Backdate coupon to after last month's invoice
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month + timezone.timedelta(days=20)
        )

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 0)
        mock_stripe.Refund.create.assert_not_called()

    @patch('billing.tasks.stripe')
    def test_coupon_created_before_invoice_date_refund_issued(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_before')
        a, _ = self._make_paying_user('mrt_a4', charge_id='ch_a4')
        b, _ = self._make_paying_user('mrt_b4', charge_id='ch_b4')
        coupon = _make_coupon('12.50', a, b)
        # Ensure coupon predates the invoice
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)

    @patch('billing.tasks.stripe')
    def test_existing_refund_record_skips_stripe_call(self, mock_stripe):
        a, inv_a = self._make_paying_user('mrt_a5', charge_id='ch_a5')
        b, inv_b = self._make_paying_user('mrt_b5', charge_id='ch_b5')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )
        # Pre-create RefundRecords for both
        RefundRecord.objects.create(
            user_coupon=coupon, stripe_invoice_id=inv_a.id,
            stripe_refund_id='re_existing_a', amount=100,
        )
        RefundRecord.objects.create(
            user_coupon=coupon, stripe_invoice_id=inv_b.id,
            stripe_refund_id='re_existing_b', amount=100,
        )

        self._run_task()

        mock_stripe.Refund.create.assert_not_called()

    @patch('billing.tasks.stripe')
    def test_stripe_error_no_record_written_task_raises(self, mock_stripe):
        import stripe as real_stripe
        mock_stripe.Refund.create.side_effect = real_stripe.error.StripeError('fail')
        a, _ = self._make_paying_user('mrt_a6', charge_id='ch_a6')
        b, _ = self._make_paying_user('mrt_b6', charge_id='ch_b6')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        with self.assertRaises(RuntimeError):
            self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 0)

    @patch('billing.tasks.stripe')
    def test_retry_after_stripe_error_succeeds_writes_record(self, mock_stripe):
        import stripe as real_stripe
        mock_stripe.Refund.create.side_effect = [
            real_stripe.error.StripeError('fail'),
            MagicMock(id='re_retry_a'),
            MagicMock(id='re_retry_b'),
        ]
        a, _ = self._make_paying_user('mrt_a7', charge_id='ch_a7')
        b, _ = self._make_paying_user('mrt_b7', charge_id='ch_b7')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        with self.assertRaises(RuntimeError):
            self._run_task()
        # Second run succeeds
        self._run_task()

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)

    @patch('billing.tasks.stripe')
    def test_partial_month_invoice_correct_refund_cents(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_partial')
        a, _ = self._make_paying_user('mrt_a8', amount_cents=400, charge_id='ch_a8')
        b, _ = self._make_paying_user('mrt_b8', amount_cents=400, charge_id='ch_b8')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        self._run_task()

        calls = mock_stripe.Refund.create.call_args_list
        amounts = [c.kwargs.get('amount') or c.args[1] if c.args else
                   c.kwargs['amount'] for c in calls]
        for amt in amounts:
            self.assertEqual(amt, 50)  # 12.5% of 400

    @patch('billing.tasks.stripe')
    def test_full_month_invoice_correct_refund_cents(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_full')
        a, _ = self._make_paying_user('mrt_a9', amount_cents=800, charge_id='ch_a9')
        b, _ = self._make_paying_user('mrt_b9', amount_cents=800, charge_id='ch_b9')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        self._run_task()

        calls = mock_stripe.Refund.create.call_args_list
        for c in calls:
            amt = c.kwargs.get('amount', c.args[1] if len(c.args) > 1 else None)
            self.assertEqual(amt, 100)  # 12.5% of 800

    @patch('billing.tasks.stripe')
    def test_admin_sentinel_never_receives_refund(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_admin')
        user, _ = self._make_paying_user('mrt_admin_u', charge_id='ch_admin_u')
        coupon = _make_coupon('20.00', user, self.admin)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        self._run_task()

        # Only one refund: for the paying user; admin gets nothing
        self.assertEqual(mock_stripe.Refund.create.call_count, 1)
        record = RefundRecord.objects.get(user_coupon=coupon)
        # The refund went to the paying user's charge
        call_kwargs = mock_stripe.Refund.create.call_args.kwargs
        self.assertEqual(call_kwargs['charge'], 'ch_admin_u')

    @patch('billing.tasks.stripe')
    def test_user_on_two_coupons_two_refunds_two_records(self, mock_stripe):
        mock_stripe.Refund.create.side_effect = [
            MagicMock(id='re_two_1'),
            MagicMock(id='re_two_2'),
        ]
        a, _ = self._make_paying_user('mrt_two_a', charge_id='ch_two_a')
        b, _ = self._make_paying_user('mrt_two_b', charge_id='ch_two_b')
        c, _ = self._make_paying_user('mrt_two_c', charge_id='ch_two_c')
        coupon1 = _make_coupon('12.50', a, b)
        coupon2 = _make_coupon('12.50', a, c)
        for cp in [coupon1, coupon2]:
            UserCoupon.objects.filter(pk=cp.pk).update(
                created_at=self.last_month - timezone.timedelta(days=5)
            )

        self._run_task()

        # a gets two refunds (one per coupon), b and c each get one
        self.assertEqual(mock_stripe.Refund.create.call_count, 4)

    @patch('billing.tasks.stripe')
    def test_full_job_retry_skips_existing_records(self, mock_stripe):
        """On retry, users with existing RefundRecords are skipped cleanly."""
        mock_stripe.Refund.create.return_value = MagicMock(id='re_idem')
        a, inv_a = self._make_paying_user('mrt_idem_a', charge_id='ch_idem_a')
        b, inv_b = self._make_paying_user('mrt_idem_b', charge_id='ch_idem_b')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        self._run_task()
        first_count = mock_stripe.Refund.create.call_count

        self._run_task()
        second_count = mock_stripe.Refund.create.call_count

        # No additional Stripe calls on second run
        self.assertEqual(first_count, second_count)
        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)


# ---------------------------------------------------------------------------
# PushCombinedDiscount — real Stripe test mode
# ---------------------------------------------------------------------------

class PushCombinedDiscount(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('pcd_user')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)
        self.stripe_sub = create_stripe_subscription(self.cust.id)
        self.track('subscription', self.stripe_sub.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            stripe_subscription_id=self.stripe_sub.id,
            status='trialing',
        )

    def tearDown(self):
        # Clean up any auto coupon we created
        try:
            s().Coupon.delete(f'nvd-auto-{self.user.pk}')
        except s().error.InvalidRequestError:
            pass
        super().tearDown()

    def test_nonzero_discount_applies_coupon(self):
        from billing.signals import _push_combined_discount
        admin = make_admin_sentinel()
        _make_coupon('20.00', self.user, admin)

        _push_combined_discount(self.local_sub)

        stripe_sub = s().Subscription.retrieve(self.stripe_sub.id)
        self.assertIsNotNone(stripe_sub.get('discount'))

    def test_zero_discount_removes_stripe_discount(self):
        from billing.signals import _push_combined_discount
        # No coupons — compute_discount returns 0
        _push_combined_discount(self.local_sub)

        stripe_sub = s().Subscription.retrieve(self.stripe_sub.id)
        self.assertIsNone(stripe_sub.get('discount'))

    def test_idempotent_second_call_no_crash(self):
        from billing.signals import _push_combined_discount
        admin = make_admin_sentinel()
        _make_coupon('20.00', self.user, admin)

        _push_combined_discount(self.local_sub)
        _push_combined_discount(self.local_sub)  # must not raise


# ---------------------------------------------------------------------------
# GenerateReferralCode
# ---------------------------------------------------------------------------

class GenerateReferralCode(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('grc_user')
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id='cus_grc_mock',
            status='active',
        )

    def test_generates_unique_code_saves_to_subscription(self):
        with patch('billing.models.stripe') as mock_stripe:
            mock_stripe.PromotionCode.create.return_value = MagicMock(id='promo_test')
            code = self.local_sub.generate_referral_code()

        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.referral_code, code)
        self.assertTrue(code.startswith('NVD-'))
        self.assertEqual(len(code), 9)  # NVD- + 5 chars

    def test_idempotent_returns_existing_code(self):
        self.local_sub.referral_code = 'NVD-EXIST'
        self.local_sub.save()

        with patch('billing.models.stripe') as mock_stripe:
            code = self.local_sub.generate_referral_code()
            mock_stripe.PromotionCode.create.assert_not_called()

        self.assertEqual(code, 'NVD-EXIST')

    def test_creates_stripe_promotion_code(self):
        """Real Stripe test mode: PromotionCode is created on Stripe."""
        real_cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', real_cust.id)
        self.local_sub.stripe_customer_id = real_cust.id
        self.local_sub.save()

        code = self.local_sub.generate_referral_code()
        self.track('promotion_code', code)

        existing = list(
            s().PromotionCode.list(code=code, active=True, limit=1).auto_paging_iter()
        )
        self.assertEqual(len(existing), 1)
        self.assertEqual(existing[0].code, code)


# ---------------------------------------------------------------------------
# SubscriptionWorkflow — adapted from old test_subscription_workflow.py
# ---------------------------------------------------------------------------

class SubscriptionWorkflow(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('sw_user')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status='cancelled',
        )

    def _create_trial_sub(self):
        stripe_sub = create_stripe_subscription(self.cust.id)
        self.track('subscription', stripe_sub.id)
        return stripe_sub

    def test_trial_sub_syncs_via_dj_stripe(self):
        """dj-stripe syncs subscription status on customer.subscription.updated."""
        from billing.signals import handle_subscription_updated
        stripe_sub = self._create_trial_sub()
        # Simulate dj-stripe having already updated local status to trialing
        self.local_sub.status = 'trialing'
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.save()
        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'trialing')
        self.assertTrue(self.local_sub.is_pro)

    def test_cancellation_syncs_to_cancelled(self):
        stripe_sub = self._create_trial_sub()
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.status = 'trialing'
        self.local_sub.save()

        s().Subscription.retrieve(stripe_sub.id).cancel()
        # Simulate dj-stripe sync
        self.local_sub.status = 'canceled'
        self.local_sub.save()
        self.local_sub.refresh_from_db()
        self.assertFalse(self.local_sub.is_pro)

    def test_any_to_active_defers_retry_jobs(self):
        self.local_sub.stripe_subscription_id = 'sub_sw_fake'
        self.local_sub.save()

        event = _make_sub_updated_event(self.cust.id, 'trialing', 'active')
        with patch('emails.tasks.retry_jobs_after_plan_upgrade') as mock_retry:
            from billing.signals import handle_subscription_updated
            handle_subscription_updated(event)
            mock_retry.defer.assert_called_once_with(user_id=self.user.pk)

    def test_active_to_active_does_not_defer_retry_jobs(self):
        self.local_sub.status = 'active'
        self.local_sub.stripe_subscription_id = 'sub_sw_fake2'
        self.local_sub.save()

        event = _make_sub_updated_event(self.cust.id, 'active', 'active')
        with patch('emails.tasks.retry_jobs_after_plan_upgrade') as mock_retry:
            from billing.signals import handle_subscription_updated
            handle_subscription_updated(event)
            mock_retry.defer.assert_not_called()

    def test_portal_session_created_for_real_customer(self):
        stripe_sub = self._create_trial_sub()
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.status = 'trialing'
        self.local_sub.save()
        session = s().billing_portal.Session.create(
            customer=self.cust.id,
            return_url='https://localhost/billing/membership/',
        )
        self.assertIn('stripe.com', session.url)

    def test_portal_view_redirects_without_stripe_subscription(self):
        from django.test import Client
        from django.urls import reverse
        self.local_sub.stripe_subscription_id = None
        self.local_sub.save()
        client = Client()
        client.force_login(self.user)
        r = client.get(reverse('billing:portal'))
        self.assertRedirects(r, reverse('billing:membership'), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# BillingWorkflow — end-to-end, real Stripe test mode
# ---------------------------------------------------------------------------

class BillingWorkflow(BillingTestCase):
    """
    Full integration tests. Stripe Refund.create is mocked to avoid needing
    a real charge (trial subs have no charge). djstripe Invoices are seeded
    directly for the refund cycle tests.
    """

    def setUp(self):
        super().setUp()
        self.admin = make_admin_sentinel()

    def _setup_user_with_stripe(self, username):
        user = make_user(username)
        cust = create_stripe_customer(user.email, username)
        self.track('customer', cust.id)
        stripe_sub = create_stripe_subscription(cust.id)
        self.track('subscription', stripe_sub.id)
        local_sub = Subscription.objects.create(
            user=user,
            stripe_customer_id=cust.id,
            stripe_subscription_id=stripe_sub.id,
            status='active',
        )
        return user, local_sub

    def test_full_referral_cycle(self):
        """
        A generates a code → B uses it → UserCoupon created →
        discount reflects 12% on both sides.
        """
        a, sub_a = self._setup_user_with_stripe('bw_a_ref')
        b, sub_b = self._setup_user_with_stripe('bw_b_ref')

        with patch('billing.models.stripe') as mock_stripe:
            mock_stripe.PromotionCode.create.return_value = MagicMock()
            code = sub_a.generate_referral_code()

        # Simulate B using the code: discount.created fires
        from billing.signals import handle_customer_discount_created
        event = _make_discount_event(code, sub_b.stripe_customer_id)
        with patch('billing.signals.stripe'):
            handle_customer_discount_created(event)

        self.assertTrue(
            UserCoupon.objects.filter(users=a).filter(users=b).exists()
        )
        self.assertEqual(compute_discount(a), 12)
        self.assertEqual(compute_discount(b), 12)

    def test_cancellation_removes_discount(self):
        a, sub_a = self._setup_user_with_stripe('bw_a_can')
        b, sub_b = self._setup_user_with_stripe('bw_b_can')
        _make_coupon('12.50', a, b)

        self.assertEqual(compute_discount(a), 12)
        sub_b.status = 'cancelled'
        sub_b.save()
        self.assertEqual(compute_discount(a), 0)

    def test_resubscribe_restores_discount(self):
        a, sub_a = self._setup_user_with_stripe('bw_a_res')
        b, sub_b = self._setup_user_with_stripe('bw_b_res')
        _make_coupon('12.50', a, b)
        sub_b.status = 'cancelled'
        sub_b.save()
        self.assertEqual(compute_discount(a), 0)

        sub_b.status = 'active'
        sub_b.save()
        self.assertEqual(compute_discount(a), 12)

    @patch('billing.tasks.stripe')
    def test_full_refund_cycle(self, mock_stripe):
        """Both active, invoices seeded, task runs → RefundRecords created."""
        mock_stripe.Refund.create.return_value = MagicMock(id='re_bw_full')
        last_month = _last_month_start()

        a, sub_a = self._setup_user_with_stripe('bw_a_rcy')
        b, sub_b = self._setup_user_with_stripe('bw_b_rcy')
        coupon = _make_coupon('12.50', a, b)
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=last_month - timezone.timedelta(days=5)
        )

        make_djstripe_invoice(a, 800, last_month, charge_id='ch_bw_a')
        make_djstripe_invoice(b, 800, last_month, charge_id='ch_bw_b')

        from billing.tasks import process_monthly_refunds
        process_monthly_refunds(int(timezone.now().timestamp()))

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)
        self.assertEqual(mock_stripe.Refund.create.call_count, 2)

        # Second run — idempotent
        process_monthly_refunds(int(timezone.now().timestamp()))
        self.assertEqual(mock_stripe.Refund.create.call_count, 2)

    def test_staff_grant(self):
        """Staff creates UserCoupon(user + admin) → 20% discount."""
        user, sub = self._setup_user_with_stripe('bw_staff_u')
        _make_coupon('20.00', user, self.admin)
        self.assertEqual(compute_discount(user), 20)

    def test_stack_staff_plus_referral(self):
        """Staff grant (20%) + active referral partner (12.5%) = 32%."""
        user, sub = self._setup_user_with_stripe('bw_stack_u')
        partner, sub_p = self._setup_user_with_stripe('bw_stack_p')
        _make_coupon('20.00', user, self.admin)
        _make_coupon('12.50', user, partner)
        self.assertEqual(compute_discount(user), 32)
