# billing/tests/test_coupon_workflow.py
"""
Real-Stripe tests for the full coupon workflow.
No mocks. Every test talks to Stripe test mode.

Bugs this exposes:
  1. sync_to_stripe() never creates a PromotionCode — code is invisible at checkout.
  2. _handle_discount_created matches coupon.id fine, but without a PromotionCode
     the webhook never fires for staff coupons (Stripe fires customer.discount.created
     only when a promo code or direct coupon is applied to a checkout session).
  3. If admin renames a Coupon.code, the PromotionCode/Coupon on Stripe are orphaned.
"""
import stripe
from django.conf import settings

from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, create_stripe_subscription, make_user, s,
)
from billing.views.webhook import _handle_discount_created, _push_combined_discount
from billing.discount import compute_discount


class CouponSyncCreatesPromotionCode(BillingTestCase):
    """
    The core bug: sync_to_stripe() creates a Stripe Coupon but no PromotionCode.
    A PromotionCode is what users type at checkout. Without it the code does nothing.
    """

    def setUp(self):
        super().setUp()
        self.coupon = Coupon.objects.create(
            code='WF-PROMO-20', percent=20, label='20% off'
        )
        self.coupon.sync_to_stripe()
        self.track('coupon', self.coupon.code)

    def test_stripe_coupon_exists_after_sync(self):
        sc = s().Coupon.retrieve(self.coupon.code)
        self.assertEqual(sc.percent_off, 20)

    def test_promotion_code_exists_after_sync(self):
        # BUG: this fails — sync_to_stripe() never calls PromotionCode.create()
        codes = s().PromotionCode.list(code=self.coupon.code, limit=5)
        active = [pc for pc in codes.auto_paging_iter() if pc.active]
        self.assertEqual(len(active), 1, 'No PromotionCode created for this coupon')

    def test_promotion_code_points_to_correct_coupon(self):
        # BUG: will fail until sync_to_stripe() creates the PromotionCode
        codes = s().PromotionCode.list(code=self.coupon.code, limit=5)
        active = [pc for pc in codes.auto_paging_iter() if pc.active]
        self.assertTrue(len(active) > 0, 'No PromotionCode to inspect')
        self.assertEqual(active[0].coupon.id, self.coupon.code)

    def test_sync_twice_does_not_duplicate_promotion_code(self):
        # second sync must be idempotent — no crash, no duplicate PromotionCode
        self.coupon.sync_to_stripe()  # second sync
        codes = s().PromotionCode.list(code=self.coupon.code, limit=5)
        active = [pc for pc in codes.auto_paging_iter() if pc.active]
        self.assertEqual(len(active), 1, 'second sync must not duplicate the PromotionCode')


class CouponAdminDeleteOrphansStripe(BillingTestCase):
    """
    Deleting a Coupon locally does not touch Stripe.
    If a PromotionCode exists, it remains active and usable after local deletion.
    """

    def setUp(self):
        super().setUp()
        self.coupon = Coupon.objects.create(
            code='WF-DEL-PROMO', percent=15, label='Del promo test'
        )
        self.coupon.sync_to_stripe()
        self.track('coupon', self.coupon.code)

    def test_local_delete_leaves_stripe_coupon(self):
        code = self.coupon.code
        self.coupon.delete()
        sc = s().Coupon.retrieve(code)
        self.assertEqual(sc.id, code)  # still there — orphaned on Stripe

    def test_local_delete_leaves_promotion_code_active(self):
        # BUG prerequisite: PromotionCode must exist first (fails until sync fixed)
        code = self.coupon.code
        self.coupon.delete()
        codes = s().PromotionCode.list(code=code, limit=5)
        active = [pc for pc in codes.auto_paging_iter() if pc.active]
        self.assertTrue(len(active) > 0, 'Promo code orphaned and still active on Stripe')


class DiscountCreatedWebhookRecordsRedemption(BillingTestCase):
    """
    _handle_discount_created: when Stripe fires customer.discount.created,
    the handler must match coupon.id against local Coupon.code and record a redemption.
    Also tests the referral code path.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('discuser')
        cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', cust.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status='cancelled',
        )
        self.coupon = Coupon.objects.create(
            code='WF-DISC-30', percent=30, label='30% off'
        )

    def _discount_obj(self, coupon_id, customer_id=None):
        return {
            'coupon': {'id': coupon_id},
            'customer': customer_id or self.local_sub.stripe_customer_id,
        }

    def test_staff_coupon_id_records_redemption(self):
        _handle_discount_created(self._discount_obj(self.coupon.code))
        self.assertTrue(
            CouponRedemption.objects.filter(user=self.user, coupon=self.coupon).exists()
        )

    def test_redemption_is_idempotent(self):
        _handle_discount_created(self._discount_obj(self.coupon.code))
        _handle_discount_created(self._discount_obj(self.coupon.code))
        self.assertEqual(
            CouponRedemption.objects.filter(user=self.user, coupon=self.coupon).count(), 1
        )

    def test_referral_code_sets_referred_by_not_redemption(self):
        referrer = make_user('referrer_disc')
        cust_r = create_stripe_customer(referrer.email, referrer.username)
        self.track('customer', cust_r.id)
        Subscription.objects.create(
            user=referrer, stripe_customer_id=cust_r.id,
            status='active', referral_code='NVD-REFTEST',
        )
        _handle_discount_created(self._discount_obj('NVD-REFTEST'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.referred_by, referrer)
        self.assertFalse(CouponRedemption.objects.filter(user=self.user).exists())

    def test_unknown_coupon_id_is_ignored(self):
        # Stripe-native coupon — not in our DB, must not raise
        _handle_discount_created(self._discount_obj('stripe-free-month-xyz'))
        self.assertFalse(CouponRedemption.objects.filter(user=self.user).exists())

    def test_missing_customer_is_ignored(self):
        _handle_discount_created({'coupon': {'id': self.coupon.code}, 'customer': None})

    def test_unknown_customer_is_ignored(self):
        _handle_discount_created(self._discount_obj(self.coupon.code, 'cus_doesnotexist'))

    def test_compute_discount_after_redemption(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        self.assertEqual(compute_discount(self.user), 30)


class PushCombinedDiscountAppliesCorrectly(BillingTestCase):
    """
    _push_combined_discount creates nvd-auto-<pk> on Stripe and applies it.
    Subscription uses charge_automatically (default) so discounts= is accepted.
    Payment method attached via test token so no actual charge occurs during trial.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('pushuser')
        cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', cust.id)
        # Attach a test payment method — required for charge_automatically + discounts
        pm = s().PaymentMethod.create(type='card', card={'token': 'tok_visa'})
        s().PaymentMethod.attach(pm.id, customer=cust.id)
        s().Customer.modify(cust.id, invoice_settings={'default_payment_method': pm.id})
        stripe_sub = s().Subscription.create(
            customer=cust.id,
            items=[{'price': settings.STRIPE_PRICE_ID}],
            trial_period_days=7,
        )
        self.track('subscription', stripe_sub.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            stripe_subscription_id=stripe_sub.id,
            status='trialing',
        )
        self.coupon = Coupon.objects.create(
            code='WF-PUSH-20', percent=20, label='Push 20%'
        )
        self.auto_id = f'nvd-auto-{self.user.pk}'
        self.track('coupon', self.auto_id)

    def _retrieve_sub(self):
        return s().Subscription.retrieve(
            self.local_sub.stripe_subscription_id,
            expand=['discount'],
        )

    def test_nonzero_discount_creates_and_applies_auto_coupon(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        _push_combined_discount(self.local_sub)
        updated = self._retrieve_sub()
        self.assertIsNotNone(updated.discount)
        self.assertEqual(updated.discount.coupon.id, self.auto_id)
        self.assertEqual(updated.discount.coupon.percent_off, 20)

    def test_zero_discount_removes_stripe_discount(self):
        s().Coupon.create(id=self.auto_id, percent_off=20, duration='once')
        s().Subscription.modify(
            self.local_sub.stripe_subscription_id,
            discounts=[{'coupon': self.auto_id}],
        )
        _push_combined_discount(self.local_sub)
        updated = self._retrieve_sub()
        self.assertIsNone(updated.get('discount'))

    def test_idempotent_second_call_does_not_raise(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        _push_combined_discount(self.local_sub)
        _push_combined_discount(self.local_sub)

    def test_auto_coupon_id_format(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        _push_combined_discount(self.local_sub)
        updated = self._retrieve_sub()
        self.assertEqual(updated.discount.coupon.id, f'nvd-auto-{self.user.pk}')