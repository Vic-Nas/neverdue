# billing/tests/signals/test_subscription_events.py
import uuid
from unittest.mock import patch

from django.test import TestCase

from billing.models import Coupon, CouponRedemption, Subscription
from billing.signals import handle_subscription_cancelled, handle_subscription_updated
from billing.tests.helpers import make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _sub(user, status='active', customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or _cus_id(),
        status=status,
    )


def _coupon(code=None, head=None):
    with patch.object(Coupon, '_push_to_stripe'):
        return Coupon.objects.create(
            code=code or f'SE{uuid.uuid4().hex[:6].upper()}',
            percent='12.50',
            head=head,
        )


def _cancelled_event(customer_id):
    obj = {'customer': customer_id}
    return type('Event', (), {'data': {'object': obj}})()


def _updated_event(customer_id, new_status, old_status=None):
    prev = {'status': old_status} if old_status else {}
    obj = {'customer': customer_id, 'status': new_status}
    return type('Event', (), {'data': {'object': obj, 'previous_attributes': prev}})()


class TestSubscriptionCancelledSignal(TestCase):

    def test_cancelled_deletes_all_redemptions_for_user(self):
        user = make_user('cancel_user')
        sub = _sub(user)
        c1, c2 = _coupon(), _coupon()
        CouponRedemption.objects.create(coupon=c1, user=user)
        CouponRedemption.objects.create(coupon=c2, user=user)
        handle_subscription_cancelled(_cancelled_event(sub.stripe_customer_id))
        self.assertFalse(CouponRedemption.objects.filter(user=user).exists())

    def test_cancelled_does_not_delete_other_users_redemptions(self):
        user = make_user('gone_user')
        other = make_user('safe_user')
        sub = _sub(user)
        coupon = _coupon()
        CouponRedemption.objects.create(coupon=coupon, user=other)
        handle_subscription_cancelled(_cancelled_event(sub.stripe_customer_id))
        self.assertTrue(CouponRedemption.objects.filter(user=other).exists())

    def test_cancelled_coupon_itself_not_deleted(self):
        user = make_user('del_user')
        sub = _sub(user)
        coupon = _coupon()
        CouponRedemption.objects.create(coupon=coupon, user=user)
        handle_subscription_cancelled(_cancelled_event(sub.stripe_customer_id))
        self.assertTrue(Coupon.objects.filter(pk=coupon.pk).exists())

    def test_cancelled_head_coupon_not_deleted(self):
        """User's own referral_coupon row survives cancellation."""
        user = make_user('head_user')
        sub = _sub(user)
        ref_coupon = _coupon(code=f'NVD-{uuid.uuid4().hex[:5].upper()}', head=user)
        sub.referral_coupon = ref_coupon
        sub.save(update_fields=['referral_coupon'])
        handle_subscription_cancelled(_cancelled_event(sub.stripe_customer_id))
        self.assertTrue(Coupon.objects.filter(pk=ref_coupon.pk).exists())

    def test_cancelled_no_redemptions_is_noop(self):
        user = make_user('no_red_user')
        sub = _sub(user)
        # Should not raise
        handle_subscription_cancelled(_cancelled_event(sub.stripe_customer_id))


class TestSubscriptionUpdatedSignal(TestCase):

    @patch('billing.signals.retry_jobs_after_plan_upgrade')
    def test_updated_active_transition_defers_retry(self, mock_task):
        user = make_user('upd_user1')
        sub = _sub(user)
        event = _updated_event(sub.stripe_customer_id, new_status='active', old_status='trialing')
        handle_subscription_updated(event)
        mock_task.defer.assert_called_once_with(user_id=user.pk)

    @patch('billing.signals.retry_jobs_after_plan_upgrade')
    def test_updated_already_active_does_not_defer(self, mock_task):
        user = make_user('upd_user2')
        sub = _sub(user)
        event = _updated_event(sub.stripe_customer_id, new_status='active', old_status='active')
        handle_subscription_updated(event)
        mock_task.defer.assert_not_called()

    @patch('billing.signals.retry_jobs_after_plan_upgrade')
    def test_updated_no_local_sub_skipped(self, mock_task):
        event = _updated_event('cus_nonexistent', new_status='active', old_status='trialing')
        handle_subscription_updated(event)
        mock_task.defer.assert_not_called()
