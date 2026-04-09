# billing/tests/test_subscription_workflow.py
"""
Full subscription workflow against real Stripe test mode.

Flow:
  1. _get_or_create_customer creates a real Stripe customer.
  2. create_stripe_subscription with trial — status becomes 'trialing'.
  3. Webhook _sync_subscription updates local DB to match Stripe.
  4. is_pro is True for trialing and active, False after cancel.
  5. Cancelling the Stripe subscription and syncing sets local status to cancelled.
  6. Portal session is created for a customer with a real subscription.
"""
import stripe

from billing.models import Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, create_stripe_subscription,
    make_user, s,
)
from billing.views.webhook import _sync_subscription


class SubscriptionTrialWorkflow(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('trialuser')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status='cancelled',
        )

    def test_trial_subscription_status_syncs_to_trialing(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)

        self.assertEqual(stripe_sub.status, 'trialing')
        _sync_subscription(stripe_sub)

        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'trialing')
        self.assertTrue(self.local_sub.is_pro)
        self.assertEqual(self.local_sub.stripe_subscription_id, stripe_sub.id)
        self.assertIsNotNone(self.local_sub.current_period_end)

    def test_active_subscription_syncs_and_is_pro(self):
        """Subscription without trial activates immediately with test card."""
        stripe_sub = create_stripe_subscription(self.cust.id)
        self.track('subscription', stripe_sub.id)

        _sync_subscription(stripe_sub)
        self.local_sub.refresh_from_db()
        self.assertIn(self.local_sub.status, ('active', 'trialing'))
        self.assertTrue(self.local_sub.is_pro)

    def test_cancellation_syncs_to_cancelled(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        _sync_subscription(stripe_sub)

        cancelled = s().Subscription.retrieve(stripe_sub.id).cancel()
        _sync_subscription(cancelled)

        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'canceled')
        self.assertFalse(self.local_sub.is_pro)

    def test_subscription_id_stored_after_sync(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        _sync_subscription(stripe_sub)

        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.stripe_subscription_id, stripe_sub.id)

    def test_period_end_stored_after_sync(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        _sync_subscription(stripe_sub)

        self.local_sub.refresh_from_db()
        self.assertIsNotNone(self.local_sub.current_period_end)


class PortalSession(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('portaluser')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)

    def test_portal_session_created_for_real_customer(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            stripe_subscription_id=stripe_sub.id,
            status='trialing',
        )
        session = s().billing_portal.Session.create(
            customer=self.cust.id,
            return_url='https://localhost/billing/plans/',
        )
        self.assertIn('stripe.com', session.url)

    def test_portal_session_fails_without_subscription(self):
        """A customer with no subscription history cannot open the portal."""
        with self.assertRaises(stripe.error.InvalidRequestError):
            s().billing_portal.Session.create(
                customer=self.cust.id,
                return_url='https://localhost/billing/plans/',
            )
