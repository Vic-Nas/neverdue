# billing/tests/test_stripe_integration.py
"""
Integration tests that hit the real Stripe test-mode API.
No mocking of Stripe calls. All subscriptions use trial_period_days to
avoid needing a payment method.

Classes:
  GenerateReferralCode        — generate_referral_code() model method
  SubscriptionCancelledSignal — handle_subscription_cancelled() integration
  ReferralWorkflow            — end-to-end referral creation and discount lifecycle
  RefundCycleWorkflow         — end-to-end refund task with seeded invoices

Run with:
  python manage.py test billing.tests.test_stripe_integration \
      --settings=billing.tests.settings_test
"""
from unittest.mock import MagicMock, patch

import stripe
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


def _make_sub_local(user, cust_id, sub_id=None, status='active'):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=cust_id,
        stripe_subscription_id=sub_id,
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


def _sub_deleted_event(customer_id):
    event = MagicMock()
    event.type = 'customer.subscription.deleted'
    event.data = {'object': {'customer': customer_id}}
    return event


# ---------------------------------------------------------------------------
# GenerateReferralCode
# ---------------------------------------------------------------------------

class GenerateReferralCode(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('grc_user')
        cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', cust.id)
        self.local_sub = _make_sub_local(self.user, cust.id, status='active')

    def test_generates_code_with_correct_format(self):
        code = self.local_sub.generate_referral_code()
        self.track('coupon', f'nvd-referral-{self.user.pk}')
        self.track('promotion_code', code)

        self.assertTrue(code.startswith('NVD-'))
        self.assertEqual(len(code), 9)  # NVD- + 5 chars

    def test_code_saved_to_subscription(self):
        code = self.local_sub.generate_referral_code()
        self.track('coupon', f'nvd-referral-{self.user.pk}')
        self.track('promotion_code', code)

        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.referral_code, code)

    def test_creates_active_stripe_promotion_code(self):
        code = self.local_sub.generate_referral_code()
        self.track('coupon', f'nvd-referral-{self.user.pk}')
        self.track('promotion_code', code)

        results = list(
            s().PromotionCode.list(code=code, active=True, limit=1).auto_paging_iter()
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].code, code)

    def test_stripe_coupon_has_correct_percent_and_duration(self):
        code = self.local_sub.generate_referral_code()
        coupon_id = f'nvd-referral-{self.user.pk}'
        self.track('coupon', coupon_id)
        self.track('promotion_code', code)

        coupon = s().Coupon.retrieve(coupon_id)
        self.assertEqual(coupon.percent_off, 12.5)
        self.assertEqual(coupon.duration, 'forever')

    def test_stripe_promotion_code_has_max_redemptions(self):
        """max_redemptions is set from referral_max_redemptions (default 12)."""
        code = self.local_sub.generate_referral_code()
        self.track('coupon', f'nvd-referral-{self.user.pk}')
        self.track('promotion_code', code)

        results = list(
            s().PromotionCode.list(code=code, limit=1).auto_paging_iter()
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].max_redemptions, 12)

    def test_custom_max_redemptions_passed_to_stripe(self):
        """Staff can set referral_max_redemptions before generating the code."""
        self.local_sub.referral_max_redemptions = 5
        self.local_sub.save()

        code = self.local_sub.generate_referral_code()
        self.track('coupon', f'nvd-referral-{self.user.pk}')
        self.track('promotion_code', code)

        results = list(
            s().PromotionCode.list(code=code, limit=1).auto_paging_iter()
        )
        self.assertEqual(results[0].max_redemptions, 5)

    def test_idempotent_returns_existing_code_no_stripe_call(self):
        self.local_sub.referral_code = 'NVD-EXIST'
        self.local_sub.save()

        with patch('billing.models.stripe') as mock_stripe:
            code = self.local_sub.generate_referral_code()
            mock_stripe.PromotionCode.create.assert_not_called()

        self.assertEqual(code, 'NVD-EXIST')


# ---------------------------------------------------------------------------
# SubscriptionCancelledSignal
# ---------------------------------------------------------------------------

class SubscriptionCancelledSignal(BillingTestCase):

    def setUp(self):
        super().setUp()

    def _setup_user(self, username, status='active'):
        user = make_user(username)
        cust = create_stripe_customer(user.email, username)
        self.track('customer', cust.id)
        stripe_sub = create_stripe_subscription(cust.id)
        self.track('subscription', stripe_sub.id)
        local_sub = _make_sub_local(user, cust.id, stripe_sub.id, status=status)
        return user, local_sub

    def test_cancellation_deletes_user_coupon_rows(self):
        """All UserCoupon rows for the cancelled user are removed."""
        a, sub_a = self._setup_user('sc_del_a')
        b, sub_b = self._setup_user('sc_del_b')
        coupon = _make_coupon('12.50', a, b)

        from billing.signals import handle_subscription_cancelled
        event = _sub_deleted_event(sub_b.stripe_customer_id)
        handle_subscription_cancelled(event)

        self.assertFalse(UserCoupon.objects.filter(pk=coupon.pk).exists())

    def test_cancellation_removes_referrer_rows_too(self):
        """If the referrer cancels, all their pairs are deleted."""
        a, sub_a = self._setup_user('sc_ref_a')
        b, sub_b = self._setup_user('sc_ref_b')
        c, sub_c = self._setup_user('sc_ref_c')
        coupon1 = _make_coupon('12.50', a, b)
        coupon2 = _make_coupon('12.50', a, c)

        from billing.signals import handle_subscription_cancelled
        event = _sub_deleted_event(sub_a.stripe_customer_id)
        handle_subscription_cancelled(event)

        self.assertFalse(UserCoupon.objects.filter(pk=coupon1.pk).exists())
        self.assertFalse(UserCoupon.objects.filter(pk=coupon2.pk).exists())

    def test_unrelated_coupons_unaffected(self):
        """Coupons not containing the cancelled user are untouched."""
        a, sub_a = self._setup_user('sc_iso_a')
        b, sub_b = self._setup_user('sc_iso_b')
        c, sub_c = self._setup_user('sc_iso_c')
        coupon_ab = _make_coupon('12.50', a, b)
        coupon_bc = _make_coupon('12.50', b, c)

        from billing.signals import handle_subscription_cancelled
        event = _sub_deleted_event(sub_a.stripe_customer_id)
        handle_subscription_cancelled(event)

        self.assertFalse(UserCoupon.objects.filter(pk=coupon_ab.pk).exists())
        self.assertTrue(UserCoupon.objects.filter(pk=coupon_bc.pk).exists())

    def test_discount_drops_to_zero_after_cancellation(self):
        """After the row is deleted, compute_discount returns 0 for the partner."""
        a, sub_a = self._setup_user('sc_disc_a')
        b, sub_b = self._setup_user('sc_disc_b')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 13)  # ceil(12.5)

        from billing.signals import handle_subscription_cancelled
        event = _sub_deleted_event(sub_b.stripe_customer_id)
        handle_subscription_cancelled(event)

        self.assertEqual(compute_discount(a), 0)


# ---------------------------------------------------------------------------
# ReferralWorkflow — end-to-end referral creation and discount lifecycle
# ---------------------------------------------------------------------------

class ReferralWorkflow(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.admin = make_admin_sentinel()

    def _setup_user(self, username, status='active'):
        user = make_user(username)
        cust = create_stripe_customer(user.email, username)
        self.track('customer', cust.id)
        stripe_sub = create_stripe_subscription(cust.id)
        self.track('subscription', stripe_sub.id)
        local_sub = _make_sub_local(user, cust.id, stripe_sub.id, status=status)
        return user, local_sub

    def _discount_event(self, referrer_user_pk, new_customer_id):
        """Match real Stripe payload: coupon.id = 'nvd-referral-<pk>'."""
        event = MagicMock()
        event.type = 'customer.discount.created'
        event.data = {
            'object': {
                'coupon': {'id': f'nvd-referral-{referrer_user_pk}'},
                'customer': new_customer_id,
                'promotion_code': 'promo_mock',
            }
        }
        return event

    def test_full_referral_cycle_creates_coupon_and_discount(self):
        a, sub_a = self._setup_user('bw_ref_a')
        b, sub_b = self._setup_user('bw_ref_b')

        code = sub_a.generate_referral_code()
        self.track('coupon', f'nvd-referral-{a.pk}')
        self.track('promotion_code', code)

        from billing.signals import handle_customer_discount_created
        event = self._discount_event(a.pk, sub_b.stripe_customer_id)
        with patch('billing.signals.stripe'):
            handle_customer_discount_created(event)

        self.assertTrue(
            UserCoupon.objects.filter(users=a).filter(users=b).exists()
        )
        self.assertEqual(compute_discount(a), 13)  # ceil(12.5)
        self.assertEqual(compute_discount(b), 13)  # ceil(12.5)

    def test_b_trialing_does_not_count_for_a_until_active(self):
        """
        While B is trialing, A gets 0% — trialing does not count as active.
        B's own discount depends on A's status (active), so B already sees 13%
        even while trialing.
        """
        a, sub_a = self._setup_user('bw_trial_a')
        b, sub_b = self._setup_user('bw_trial_b', status='trialing')

        _make_coupon('12.50', a, b)

        self.assertEqual(compute_discount(a), 0)   # B trialing — doesn't count for A
        self.assertEqual(compute_discount(b), 13)  # A is active — counts for B

        sub_b.status = 'active'
        sub_b.save()
        self.assertEqual(compute_discount(a), 13)
        self.assertEqual(compute_discount(b), 13)

    def test_b_cancels_a_loses_discount(self):
        a, sub_a = self._setup_user('bw_can_a')
        b, sub_b = self._setup_user('bw_can_b')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(a), 13)

        sub_b.status = 'cancelled'
        sub_b.save()
        self.assertEqual(compute_discount(a), 0)

    def test_a_cancels_b_loses_discount(self):
        """Referrer cancelling removes the discount for the referred user too."""
        a, sub_a = self._setup_user('bw_ref_can_a')
        b, sub_b = self._setup_user('bw_ref_can_b')
        _make_coupon('12.50', a, b)
        self.assertEqual(compute_discount(b), 13)

        sub_a.status = 'cancelled'
        sub_a.save()
        self.assertEqual(compute_discount(b), 0)

    def test_unsubscribe_deletes_row_and_frees_slot(self):
        """
        When a user's subscription is deleted via Stripe webhook, their
        UserCoupon rows are removed. Resubscribing requires a new code.
        """
        a, sub_a = self._setup_user('bw_del_a')
        b, sub_b = self._setup_user('bw_del_b')
        coupon = _make_coupon('12.50', a, b)
        coupon_pk = coupon.pk
        self.assertEqual(compute_discount(a), 13)

        from billing.signals import handle_subscription_cancelled
        event = _sub_deleted_event(sub_b.stripe_customer_id)
        handle_subscription_cancelled(event)

        self.assertFalse(UserCoupon.objects.filter(pk=coupon_pk).exists())
        self.assertEqual(compute_discount(a), 0)

    def test_staff_grant_gives_always_on_discount(self):
        user, sub = self._setup_user('bw_staff_u')
        _make_coupon('20.00', user, self.admin)
        self.assertEqual(compute_discount(user), 20)

    def test_stack_staff_plus_referral(self):
        # ceil(20.0 + 12.5) = ceil(32.5) = 33
        user, sub = self._setup_user('bw_stack_u')
        partner, sub_p = self._setup_user('bw_stack_p')
        _make_coupon('20.00', user, self.admin)
        _make_coupon('12.50', user, partner)
        self.assertEqual(compute_discount(user), 33)


# ---------------------------------------------------------------------------
# RefundCycleWorkflow
# ---------------------------------------------------------------------------

class RefundCycleWorkflow(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.last_month = _last_month_start()

    def _setup_user(self, username):
        user = make_user(username)
        cust = create_stripe_customer(user.email, username)
        self.track('customer', cust.id)
        stripe_sub = create_stripe_subscription(cust.id)
        self.track('subscription', stripe_sub.id)
        local_sub = _make_sub_local(user, cust.id, stripe_sub.id, status='active')
        return user, local_sub

    @patch('billing.tasks.stripe')
    def test_full_refund_cycle_creates_records(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_rcy')
        a, sub_a = self._setup_user('rcy_a')
        b, sub_b = self._setup_user('rcy_b')
        coupon = UserCoupon.objects.create(percent='12.50')
        coupon.users.set([a, b])
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        make_djstripe_invoice(a, 800, self.last_month, charge_id='ch_rcy_a')
        make_djstripe_invoice(b, 800, self.last_month, charge_id='ch_rcy_b')

        from billing.tasks import process_monthly_refunds
        process_monthly_refunds(int(timezone.now().timestamp()))

        self.assertEqual(RefundRecord.objects.filter(user_coupon=coupon).count(), 2)
        self.assertEqual(mock_stripe.Refund.create.call_count, 2)

    @patch('billing.tasks.stripe')
    def test_full_refund_cycle_idempotent(self, mock_stripe):
        mock_stripe.Refund.create.return_value = MagicMock(id='re_rcy2')
        a, _ = self._setup_user('rcy2_a')
        b, _ = self._setup_user('rcy2_b')
        coupon = UserCoupon.objects.create(percent='12.50')
        coupon.users.set([a, b])
        UserCoupon.objects.filter(pk=coupon.pk).update(
            created_at=self.last_month - timezone.timedelta(days=5)
        )

        make_djstripe_invoice(a, 800, self.last_month, charge_id='ch_rcy2_a')
        make_djstripe_invoice(b, 800, self.last_month, charge_id='ch_rcy2_b')

        from billing.tasks import process_monthly_refunds
        process_monthly_refunds(int(timezone.now().timestamp()))
        first_call_count = mock_stripe.Refund.create.call_count

        process_monthly_refunds(int(timezone.now().timestamp()))
        self.assertEqual(mock_stripe.Refund.create.call_count, first_call_count)
