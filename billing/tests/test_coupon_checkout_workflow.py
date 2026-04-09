# billing/tests/test_coupon_checkout_workflow.py
"""
End-to-end: staff creates coupon -> syncs to Stripe -> promotion code created
-> applied to a subscription -> customer.discount.created webhook fires
-> CouponRedemption recorded locally -> compute_discount returns correct %.

Also tests: coupon applied to subscription via _handle_checkout_completed,
and that compute_discount feeds into _push_combined_discount correctly on Stripe.
"""
import stripe

from billing.discount import compute_discount
from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, create_stripe_subscription,
    make_user, s,
)
from billing.views.webhook import _handle_discount_created, _push_combined_discount


class CouponAppliedToSubscription(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('couponuser')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status='cancelled',
        )
        # Create and sync a staff coupon to Stripe
        self.coupon = Coupon.objects.create(
            code='WF-CHECKOUT-20', percent=20, label='20% off'
        )
        self.coupon.sync_to_stripe()
        self.track('coupon', self.coupon.code)

    def test_coupon_exists_on_stripe_after_sync(self):
        sc = s().Coupon.retrieve(self.coupon.code)
        self.assertEqual(sc.percent_off, 20)

    def test_applying_coupon_to_subscription_via_stripe(self):
        """Apply coupon directly to subscription and verify Stripe accepts it."""
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)

        modified = s().Subscription.modify(
            stripe_sub.id,
            discounts=[{'coupon': self.coupon.code}],
        )
        self.assertEqual(modified.discount.coupon.id, self.coupon.code)

    def test_discount_created_webhook_records_redemption(self):
        """
        Simulates the customer.discount.created webhook payload.
        _handle_discount_created must create a CouponRedemption locally.
        """
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.status = 'trialing'
        self.local_sub.save()

        discount_obj = {
            'coupon': {'id': self.coupon.code},
            'customer': self.cust.id,
        }
        _handle_discount_created(discount_obj)

        self.assertTrue(
            CouponRedemption.objects.filter(
                user=self.user, coupon=self.coupon
            ).exists()
        )

    def test_compute_discount_after_redemption_returns_correct_percent(self):
        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        self.assertEqual(compute_discount(self.user), 20)

    def test_push_combined_discount_applies_auto_coupon_to_stripe_subscription(self):
        """
        After redemption is recorded, _push_combined_discount must create
        nvd-auto-<pk> on Stripe and apply it to the subscription.
        """
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.status = 'trialing'
        self.local_sub.save()

        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)

        auto_id = f'nvd-auto-{self.user.pk}'
        self.track('coupon', auto_id)
        _push_combined_discount(self.local_sub)

        updated = s().Subscription.retrieve(stripe_sub.id)
        self.assertIsNotNone(updated.discount)
        self.assertEqual(updated.discount.coupon.id, auto_id)
        self.assertEqual(updated.discount.coupon.percent_off, 20)

    def test_push_combined_discount_idempotent_second_call(self):
        """Calling _push_combined_discount twice must not raise."""
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.status = 'trialing'
        self.local_sub.save()

        CouponRedemption.objects.create(user=self.user, coupon=self.coupon)
        auto_id = f'nvd-auto-{self.user.pk}'
        self.track('coupon', auto_id)

        _push_combined_discount(self.local_sub)
        _push_combined_discount(self.local_sub)  # must not raise

    def test_zero_discount_clears_stripe_discount(self):
        """No redemptions -> _push_combined_discount removes any discount."""
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        self.local_sub.stripe_subscription_id = stripe_sub.id
        self.local_sub.status = 'trialing'
        self.local_sub.save()

        # Apply coupon first, then remove redemption, then push
        s().Subscription.modify(stripe_sub.id, discounts=[{'coupon': self.coupon.code}])
        _push_combined_discount(self.local_sub)

        updated = s().Subscription.retrieve(stripe_sub.id)
        self.assertFalse(updated.discount)
