# billing/tests/test_signals.py
"""
Tests for billing signal handlers.

Events are constructed to match real Stripe payloads:
  customer.discount.created — coupon.id = 'nvd-referral-<pk>' (the Stripe
    Coupon ID, NOT the human-readable NVD-XXXXX promotion code string).
  customer.subscription.deleted — fires when a subscription is fully cancelled.
  customer.subscription.updated — fires on any subscription change.

Stripe API calls are mocked throughout; no live Stripe calls here.

Run with:
  python manage.py test billing.tests.test_signals \
      --settings=billing.tests.settings_test
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import Subscription, UserCoupon, compute_discount
from billing.tests.helpers import make_admin_sentinel, make_user


# ---------------------------------------------------------------------------
# Event factories — match real Stripe payload shapes
# ---------------------------------------------------------------------------

def _discount_event(referrer_user_pk, new_customer_id):
    """
    customer.discount.created event as Stripe actually sends it.
    coupon.id is the Stripe Coupon object ID: 'nvd-referral-<pk>'.
    The human-readable 'NVD-XXXXX' string is on the PromotionCode object,
    which is NOT included inline in this event.
    """
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


def _unknown_coupon_event(coupon_id, customer_id):
    """A discount event with a coupon that doesn't belong to us."""
    event = MagicMock()
    event.type = 'customer.discount.created'
    event.data = {
        'object': {
            'coupon': {'id': coupon_id},
            'customer': customer_id,
        }
    }
    return event


def _sub_updated_event(customer_id, old_status, new_status):
    event = MagicMock()
    event.type = 'customer.subscription.updated'
    event.data = {
        'object': {'customer': customer_id, 'status': new_status},
        'previous_attributes': {'status': old_status},
    }
    return event


def _sub_deleted_event(customer_id):
    event = MagicMock()
    event.type = 'customer.subscription.deleted'
    event.data = {'object': {'customer': customer_id}}
    return event


# ---------------------------------------------------------------------------
# Shared setup helper
# ---------------------------------------------------------------------------

def _make_sub(user, status='active', stripe_customer_id=None, stripe_subscription_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=stripe_customer_id or f'cus_{user.username}',
        stripe_subscription_id=stripe_subscription_id or f'sub_{user.username}',
        status=status,
    )


# ---------------------------------------------------------------------------
# customer.discount.created
# ---------------------------------------------------------------------------

class DiscountCreatedSignal(TestCase):

    def setUp(self):
        super().setUp()
        from billing.signals import handle_customer_discount_created
        self.handle = handle_customer_discount_created

    def test_known_referral_creates_user_coupon(self):
        referrer = make_user('sig_ref1')
        new_user = make_user('sig_new1')
        _make_sub(referrer, stripe_customer_id='cus_ref1')
        _make_sub(new_user, stripe_customer_id='cus_new1')

        event = _discount_event(referrer.pk, 'cus_new1')
        with patch('billing.signals.stripe'):
            self.handle(event)

        self.assertTrue(
            UserCoupon.objects.filter(users=referrer).filter(users=new_user).exists()
        )

    def test_created_coupon_has_correct_percent(self):
        referrer = make_user('sig_ref_pct')
        new_user = make_user('sig_new_pct')
        _make_sub(referrer, stripe_customer_id='cus_ref_pct')
        _make_sub(new_user, stripe_customer_id='cus_new_pct')

        event = _discount_event(referrer.pk, 'cus_new_pct')
        with patch('billing.signals.stripe'):
            self.handle(event)

        coupon = UserCoupon.objects.get(users=referrer)
        self.assertEqual(float(coupon.percent), 12.50)

    def test_coupon_id_must_match_nvd_referral_prefix(self):
        """Coupon IDs not starting with 'nvd-referral-' are ignored."""
        user = make_user('sig_unk1')
        _make_sub(user, stripe_customer_id='cus_unk1')

        event = _unknown_coupon_event('UNKNOWN-CODE', 'cus_unk1')
        with patch('billing.signals.stripe'):
            self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())

    def test_nvd_auto_coupon_is_ignored(self):
        """nvd-auto-<pk> must not be treated as referrals."""
        user = make_user('sig_auto1')
        _make_sub(user, stripe_customer_id='cus_auto1')

        event = _unknown_coupon_event(f'nvd-auto-{user.pk}', 'cus_auto1')
        with patch('billing.signals.stripe'):
            self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())

    def test_malformed_referral_pk_is_ignored(self):
        """nvd-referral-<non-int> must not raise, just log and return."""
        user = make_user('sig_bad1')
        _make_sub(user, stripe_customer_id='cus_bad1')

        event = _unknown_coupon_event('nvd-referral-notanint', 'cus_bad1')
        with patch('billing.signals.stripe'):
            self.handle(event)  # must not raise

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())

    def test_unknown_referrer_pk_is_ignored(self):
        """nvd-referral-<pk> where pk has no Subscription is silently skipped."""
        user = make_user('sig_nopk1')
        _make_sub(user, stripe_customer_id='cus_nopk1')

        event = _discount_event(99999999, 'cus_nopk1')
        with patch('billing.signals.stripe'):
            self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())

    def test_self_referral_blocked_and_stripe_discount_deleted(self):
        user = make_user('sig_self1')
        _make_sub(user, stripe_customer_id='cus_self1')

        event = _discount_event(user.pk, 'cus_self1')
        with patch('billing.signals.stripe') as mock_stripe:
            self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())
        mock_stripe.Customer.delete_discount.assert_called_once_with('cus_self1')

    def test_duplicate_webhook_creates_exactly_one_coupon(self):
        referrer = make_user('sig_ref2')
        new_user = make_user('sig_new2')
        _make_sub(referrer, stripe_customer_id='cus_ref2')
        _make_sub(new_user, stripe_customer_id='cus_new2')

        event = _discount_event(referrer.pk, 'cus_new2')
        with patch('billing.signals.stripe'):
            self.handle(event)
            self.handle(event)  # second call — must be idempotent

        self.assertEqual(
            UserCoupon.objects.filter(users=referrer).filter(users=new_user).count(), 1
        )

    def test_second_person_using_same_referral_code_creates_new_coupon(self):
        """A's code can be used by multiple people — each gets a separate UserCoupon."""
        referrer = make_user('sig_multi_ref')
        user_b = make_user('sig_multi_b')
        user_c = make_user('sig_multi_c')
        _make_sub(referrer, stripe_customer_id='cus_multi_ref')
        _make_sub(user_b, stripe_customer_id='cus_multi_b')
        _make_sub(user_c, stripe_customer_id='cus_multi_c')

        with patch('billing.signals.stripe'):
            self.handle(_discount_event(referrer.pk, 'cus_multi_b'))
            self.handle(_discount_event(referrer.pk, 'cus_multi_c'))

        self.assertTrue(
            UserCoupon.objects.filter(users=referrer).filter(users=user_b).exists()
        )
        self.assertTrue(
            UserCoupon.objects.filter(users=referrer).filter(users=user_c).exists()
        )
        # B and C must NOT be linked to each other
        self.assertFalse(
            UserCoupon.objects.filter(users=user_b).filter(users=user_c).exists()
        )


# ---------------------------------------------------------------------------
# customer.subscription.deleted
# ---------------------------------------------------------------------------

class SubscriptionCancelledSignal(TestCase):

    def setUp(self):
        super().setUp()
        from billing.signals import handle_subscription_cancelled
        self.handle = handle_subscription_cancelled

    def test_unsubscribe_deletes_all_user_coupon_rows(self):
        """When a user's subscription is deleted, all their UserCoupon rows go."""
        referrer = make_user('sig_del_ref')
        user = make_user('sig_del_user')
        _make_sub(referrer, stripe_customer_id='cus_del_ref')
        _make_sub(user, stripe_customer_id='cus_del_user')

        coupon = UserCoupon.objects.create(percent='12.50')
        coupon.users.set([referrer, user])

        event = _sub_deleted_event('cus_del_user')
        self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(users=user).exists())

    def test_unsubscribe_also_removes_referrer_rows(self):
        """If the referrer unsubscribes, their pairs are deleted too — partners lose the slot."""
        referrer = make_user('sig_del_ref2')
        user_b = make_user('sig_del_b2')
        user_c = make_user('sig_del_c2')
        _make_sub(referrer, stripe_customer_id='cus_del_ref2')
        _make_sub(user_b, stripe_customer_id='cus_del_b2')
        _make_sub(user_c, stripe_customer_id='cus_del_c2')

        coupon1 = UserCoupon.objects.create(percent='12.50')
        coupon1.users.set([referrer, user_b])
        coupon2 = UserCoupon.objects.create(percent='12.50')
        coupon2.users.set([referrer, user_c])

        event = _sub_deleted_event('cus_del_ref2')
        self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(users=referrer).exists())
        # B and C no longer have the coupon either
        self.assertFalse(UserCoupon.objects.filter(pk=coupon1.pk).exists())
        self.assertFalse(UserCoupon.objects.filter(pk=coupon2.pk).exists())

    def test_unsubscribe_does_not_affect_other_users_unrelated_coupons(self):
        """Unrelated UserCoupon rows (not containing the cancelled user) are untouched."""
        user_a = make_user('sig_del_iso_a')
        user_b = make_user('sig_del_iso_b')
        user_c = make_user('sig_del_iso_c')
        _make_sub(user_a, stripe_customer_id='cus_del_iso_a')
        _make_sub(user_b, stripe_customer_id='cus_del_iso_b')
        _make_sub(user_c, stripe_customer_id='cus_del_iso_c')

        coupon_ab = UserCoupon.objects.create(percent='12.50')
        coupon_ab.users.set([user_a, user_b])
        coupon_bc = UserCoupon.objects.create(percent='12.50')
        coupon_bc.users.set([user_b, user_c])

        # A unsubscribes — only coupon_ab is deleted
        event = _sub_deleted_event('cus_del_iso_a')
        self.handle(event)

        self.assertFalse(UserCoupon.objects.filter(pk=coupon_ab.pk).exists())
        self.assertTrue(UserCoupon.objects.filter(pk=coupon_bc.pk).exists())

    def test_unsubscribe_unknown_customer_no_error(self):
        event = _sub_deleted_event('cus_nonexistent')
        self.handle(event)  # must not raise

    def test_unsubscribe_user_with_no_coupons_no_error(self):
        user = make_user('sig_del_nocoupon')
        _make_sub(user, stripe_customer_id='cus_del_nocoupon')

        event = _sub_deleted_event('cus_del_nocoupon')
        self.handle(event)  # must not raise


# ---------------------------------------------------------------------------
# customer.subscription.updated
# ---------------------------------------------------------------------------

class SubscriptionUpdatedSignal(TestCase):

    def setUp(self):
        super().setUp()
        from billing.signals import handle_subscription_updated
        self.handle = handle_subscription_updated

    def test_any_to_active_defers_retry_jobs(self):
        user = make_user('sig_su1')
        _make_sub(user, stripe_customer_id='cus_su1', status='trialing')

        event = _sub_updated_event('cus_su1', 'trialing', 'active')
        with patch('billing.signals.retry_jobs_after_plan_upgrade') as mock_retry:
            self.handle(event)
            mock_retry.defer.assert_called_once_with(user_id=user.pk)

    def test_active_to_active_does_not_defer(self):
        user = make_user('sig_su2')
        _make_sub(user, stripe_customer_id='cus_su2', status='active')

        event = _sub_updated_event('cus_su2', 'active', 'active')
        with patch('billing.signals.retry_jobs_after_plan_upgrade') as mock_retry:
            self.handle(event)
            mock_retry.defer.assert_not_called()

    def test_cancelled_to_active_defers_retry(self):
        user = make_user('sig_su3')
        _make_sub(user, stripe_customer_id='cus_su3', status='cancelled')

        event = _sub_updated_event('cus_su3', 'cancelled', 'active')
        with patch('billing.signals.retry_jobs_after_plan_upgrade') as mock_retry:
            self.handle(event)
            mock_retry.defer.assert_called_once_with(user_id=user.pk)

    def test_unknown_customer_no_error(self):
        event = _sub_updated_event('cus_nonexistent', 'trialing', 'active')
        self.handle(event)  # must not raise
