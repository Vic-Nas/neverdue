# billing/tests/integration/test_full_flow.py
"""
Integration tests exercising the full billing flow using real model operations.
Stripe API calls are mocked (we don't want real charges), but all Django-side
logic runs against a real test database.
"""
import time
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import Coupon, CouponRedemption, RefundRecord, Subscription
from billing.signals import handle_customer_discount_created, handle_subscription_cancelled
from billing.tasks import _prev_month_window, process_monthly_refunds
from billing.tests.helpers import make_djstripe_invoice, make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _sub(user, status='active', customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or _cus_id(),
        status=status,
    )


def _event(customer_id, code=None, coupon_id=None):
    obj = {'customer': customer_id}
    if code:
        obj['promotion_code'] = {'code': code}
    if coupon_id:
        obj['coupon'] = {'id': coupon_id}
    return type('Event', (), {'data': {'object': obj}})()


def _prev_month_start():
    now = timezone.now()
    start, _ = _prev_month_window(now)
    return start + timezone.timedelta(days=15)


class TestReferralFlow(TestCase):
    """subscribe → redeem → refund (both redeemer and head)."""

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id='re_full_flow'))
    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_referral_flow(self, _mock_del, mock_refund):
        head = make_user('fl_head')
        redeemer = make_user('fl_redeemer')
        head_sub = _sub(head)
        red_sub = _sub(redeemer)

        # Head generates referral coupon
        coupon = Coupon.objects.create(
            code=f'NVD-{uuid.uuid4().hex[:5].upper()}',
            percent='12.50',
            max_redemptions=12,
            head=head,
        )
        head_sub.referral_coupon = coupon
        head_sub.save(update_fields=['referral_coupon'])

        # Redeemer enters code at checkout → webhook fires
        event = _event(red_sub.stripe_customer_id, code=coupon.code)
        handle_customer_discount_created(event)

        redemption = CouponRedemption.objects.get(coupon=coupon, user=redeemer)
        self.assertIsNotNone(redemption)

        # Seed invoices for both users in prev month
        period_start = _prev_month_start()
        make_djstripe_invoice(head, 800, period_start, charge_id='ch_fl_head')
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_fl_red')

        # Run monthly task
        process_monthly_refunds(int(time.time()))

        # Both redeemer and head RefundRecords exist
        self.assertTrue(RefundRecord.objects.filter(redemption=redemption).exists())
        self.assertTrue(RefundRecord.objects.filter(coupon_head=coupon).exists())


class TestStaffGrantFlow(TestCase):
    """Staff grant (head=None) → redeemer gets refund, no head RefundRecord."""

    @patch('billing.tasks.stripe.Refund.create', return_value=MagicMock(id='re_grant'))
    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_staff_grant_flow(self, _mock_del, mock_refund):
        redeemer = make_user('sg_redeemer')
        red_sub = _sub(redeemer)

        coupon = Coupon.objects.create(
            code=f'GRANT{uuid.uuid4().hex[:5].upper()}',
            percent='30.00',
            head=None,
        )

        # Webhook fires
        event = _event(red_sub.stripe_customer_id, code=coupon.code)
        handle_customer_discount_created(event)
        redemption = CouponRedemption.objects.get(coupon=coupon, user=redeemer)

        period_start = _prev_month_start()
        make_djstripe_invoice(redeemer, 800, period_start, charge_id='ch_sg_red')

        process_monthly_refunds(int(time.time()))

        self.assertTrue(RefundRecord.objects.filter(redemption=redemption).exists())
        self.assertFalse(RefundRecord.objects.filter(coupon_head=coupon).exists())


class TestUnsubClearsRedemption(TestCase):

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_unsub_clears_redemption(self, _mock_del):
        head = make_user('unsub_head')
        redeemer = make_user('unsub_red')
        head_sub = _sub(head)
        red_sub = _sub(redeemer)

        coupon = Coupon.objects.create(
            code=f'UNSUB{uuid.uuid4().hex[:5].upper()}',
            percent='12.50',
            head=head,
        )

        # Redeem
        event = _event(red_sub.stripe_customer_id, code=coupon.code)
        handle_customer_discount_created(event)
        self.assertTrue(CouponRedemption.objects.filter(coupon=coupon, user=redeemer).exists())

        # Cancel subscription → signal deletes redemption
        cancel_event = type('Event', (), {
            'data': {'object': {'customer': red_sub.stripe_customer_id}}
        })()
        handle_subscription_cancelled(cancel_event)

        self.assertFalse(CouponRedemption.objects.filter(coupon=coupon, user=redeemer).exists())
        # Coupon itself still exists
        self.assertTrue(Coupon.objects.filter(pk=coupon.pk).exists())


class TestResubWithNewCode(TestCase):

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_resub_with_new_code(self, _mock_del):
        redeemer = make_user('resub_red')
        red_sub = _sub(redeemer)

        coupon_old = Coupon.objects.create(
            code=f'OLD{uuid.uuid4().hex[:5].upper()}',
            percent='10.00',
        )
        coupon_new = Coupon.objects.create(
            code=f'NEW{uuid.uuid4().hex[:5].upper()}',
            percent='15.00',
        )

        # First subscription
        CouponRedemption.objects.create(coupon=coupon_old, user=redeemer)

        # Unsub → clear old redemption
        cancel_event = type('Event', (), {
            'data': {'object': {'customer': red_sub.stripe_customer_id}}
        })()
        handle_subscription_cancelled(cancel_event)
        self.assertFalse(CouponRedemption.objects.filter(coupon=coupon_old, user=redeemer).exists())

        # Resub with new code
        event = _event(red_sub.stripe_customer_id, code=coupon_new.code)
        handle_customer_discount_created(event)
        self.assertTrue(CouponRedemption.objects.filter(coupon=coupon_new, user=redeemer).exists())


class TestSelfReferralBlockedEndToEnd(TestCase):

    @patch('billing.signals.stripe.Customer.delete_discount')
    def test_self_referral_blocked_end_to_end(self, mock_del):
        user = make_user('self_ref')
        sub = _sub(user)

        coupon = Coupon.objects.create(
            code=f'NVD-{uuid.uuid4().hex[:5].upper()}',
            percent='12.50',
            head=user,
        )

        # User tries to redeem own code
        event = _event(sub.stripe_customer_id, code=coupon.code)
        handle_customer_discount_created(event)

        # No redemption created
        self.assertFalse(CouponRedemption.objects.filter(coupon=coupon, user=user).exists())
        # Stripe discount was deleted
        mock_del.assert_called_once_with(sub.stripe_customer_id)
