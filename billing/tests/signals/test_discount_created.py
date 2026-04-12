# billing/tests/signals/test_discount_created.py
"""
Tests for handle_customer_discount_created.
All tests call the handler directly (no webhook infrastructure needed).
stripe.Customer.delete_discount is mocked.
"""
import uuid
from unittest.mock import patch

from django.test import TestCase

from billing.models import Coupon, CouponRedemption, Subscription
from billing.signals import handle_customer_discount_created
from billing.tests.helpers import make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _coupon(code=None, head=None):
    with patch.object(Coupon, '_push_to_stripe'):
        return Coupon.objects.create(
            code=code or f'SIG{uuid.uuid4().hex[:5].upper()}',
            percent='12.50',
            head=head,
        )


def _sub(user, customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or _cus_id(),
        status='active',
    )


def _event(customer_id, code=None, coupon_id=None):
    """Build a minimal event dict matching what the handler reads."""
    obj = {'customer': customer_id}
    if code:
        obj['promotion_code'] = {'code': code}
    if coupon_id:
        obj['coupon'] = {'id': coupon_id}
    return type('Event', (), {'data': {'object': obj}})()


class TestDiscountCreatedSignal(TestCase):

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_creates_redemption_on_valid_code(self, _mock_del):
        user = make_user('alice')
        sub = _sub(user)
        coupon = _coupon(code='PROMO1')
        event = _event(sub.stripe_customer_id, code='PROMO1')
        handle_customer_discount_created(event)
        self.assertTrue(CouponRedemption.objects.filter(coupon=coupon, user=user).exists())

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_creates_redemption_code_case_insensitive(self, _mock_del):
        user = make_user('bob')
        sub = _sub(user)
        coupon = _coupon(code='MYCODE')
        event = _event(sub.stripe_customer_id, code='mycode')
        handle_customer_discount_created(event)
        self.assertTrue(CouponRedemption.objects.filter(coupon=coupon, user=user).exists())

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_self_referral_blocked_and_stripe_discount_deleted(self, mock_del):
        user = make_user('carol')
        sub = _sub(user)
        coupon = _coupon(code='NVD-CAROL', head=user)
        event = _event(sub.stripe_customer_id, code='NVD-CAROL')
        handle_customer_discount_created(event)
        mock_del.assert_called_once_with(sub.stripe_customer_id)
        self.assertFalse(CouponRedemption.objects.filter(coupon=coupon, user=user).exists())

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_self_referral_on_non_referral_coupon_allowed(self, mock_del):
        """head ≠ new user → allowed (not self-referral)."""
        head = make_user('headu')
        redeemer = make_user('redu')
        sub = _sub(redeemer)
        coupon = _coupon(code='SHARED1', head=head)
        event = _event(sub.stripe_customer_id, code='SHARED1')
        handle_customer_discount_created(event)
        mock_del.assert_not_called()
        self.assertTrue(CouponRedemption.objects.filter(coupon=coupon, user=redeemer).exists())

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_duplicate_redemption_skipped(self, _mock_del):
        """Same (coupon, user) already exists — no second row created."""
        user = make_user('dave')
        sub = _sub(user)
        coupon = _coupon(code='DUP1')
        CouponRedemption.objects.create(coupon=coupon, user=user)
        event = _event(sub.stripe_customer_id, code='DUP1')
        handle_customer_discount_created(event)
        self.assertEqual(CouponRedemption.objects.filter(coupon=coupon, user=user).count(), 1)

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_unknown_code_skipped_silently(self, mock_del):
        user = make_user('eve')
        sub = _sub(user)
        event = _event(sub.stripe_customer_id, code='NOSUCHCODE')
        handle_customer_discount_created(event)
        mock_del.assert_not_called()
        self.assertFalse(CouponRedemption.objects.filter(user=user).exists())

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_no_local_subscription_for_customer_skipped(self, mock_del):
        _coupon(code='ORPHAN1')
        event = _event('cus_nonexistent', code='ORPHAN1')
        handle_customer_discount_created(event)
        self.assertFalse(CouponRedemption.objects.filter(coupon__code='ORPHAN1').exists())

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_missing_customer_field_skipped(self, mock_del):
        event = type('Event', (), {'data': {'object': {'promotion_code': {'code': 'X'}}}})()
        handle_customer_discount_created(event)
        mock_del.assert_not_called()

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_missing_code_field_skipped(self, mock_del):
        user = make_user('frank')
        sub = _sub(user)
        event = type('Event', (), {'data': {'object': {'customer': sub.stripe_customer_id}}})()
        handle_customer_discount_created(event)
        mock_del.assert_not_called()

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_nvd_prefix_code_derived_from_coupon_id(self, _mock_del):
        """Fallback: promotion_code not expanded — derive code from coupon.id nvd-<code>."""
        user = make_user('grace')
        sub = _sub(user)
        coupon = _coupon(code='NVDFALLBACK')
        # No promotion_code field; coupon.id = 'nvd-nvdfallback'
        obj = {'customer': sub.stripe_customer_id, 'coupon': {'id': 'nvd-nvdfallback'}}
        event = type('Event', (), {'data': {'object': obj}})()
        handle_customer_discount_created(event)
        self.assertTrue(CouponRedemption.objects.filter(coupon=coupon, user=user).exists())
