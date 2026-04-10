# billing/tests/test_subscription_workflow.py
"""
Full subscription workflow against real Stripe test mode.

Bug fix vs previous version:
  retry_jobs_after_plan_upgrade is imported INSIDE _sync_subscription(),
  so patch('billing.views.webhook.retry_jobs_after_plan_upgrade') fails —
  the name doesn't exist at module level. Correct target is emails.tasks.
"""
import stripe
from unittest.mock import patch

from billing.models import Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, create_stripe_subscription, make_user, s,
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
        # Correct patch target: the name where it's defined, not where it's imported
        patcher = patch('emails.tasks.retry_jobs_after_plan_upgrade')
        self.mock_retry = patcher.start()
        self.addCleanup(patcher.stop)

    def _create_trial_sub(self):
        stripe_sub = create_stripe_subscription(self.cust.id)
        self.track('subscription', stripe_sub.id)
        return stripe_sub

    def test_trial_sub_syncs_to_trialing(self):
        stripe_sub = self._create_trial_sub()
        self.assertEqual(stripe_sub.status, 'trialing')
        _sync_subscription(stripe_sub)
        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'trialing')
        self.assertTrue(self.local_sub.is_pro)
        self.assertEqual(self.local_sub.stripe_subscription_id, stripe_sub.id)
        self.assertIsNotNone(self.local_sub.current_period_end)

    def test_cancellation_syncs_to_cancelled(self):
        stripe_sub = self._create_trial_sub()
        _sync_subscription(stripe_sub)
        cancelled = s().Subscription.retrieve(stripe_sub.id).cancel()
        _sync_subscription(cancelled)
        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'canceled')
        self.assertFalse(self.local_sub.is_pro)

    def test_subscription_id_stored_after_sync(self):
        stripe_sub = self._create_trial_sub()
        _sync_subscription(stripe_sub)
        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.stripe_subscription_id, stripe_sub.id)

    def test_period_end_stored_after_sync(self):
        stripe_sub = self._create_trial_sub()
        _sync_subscription(stripe_sub)
        self.local_sub.refresh_from_db()
        self.assertIsNotNone(self.local_sub.current_period_end)

    def test_cancelled_to_active_defers_retry_jobs(self):
        fake_active = {
            'id': 'sub_fakeactive',
            'customer': self.cust.id,
            'status': 'active',
            'items': {'data': [{'current_period_end': 9999999999}]},
        }
        with patch('billing.views.webhook._push_combined_discount'):
            _sync_subscription(fake_active)
        self.mock_retry.defer.assert_called_once_with(user_id=self.user.pk)

    def test_active_to_active_does_not_defer_retry_jobs(self):
        self.local_sub.status = 'active'
        self.local_sub.save()
        fake_active = {
            'id': 'sub_fakestillactive',
            'customer': self.cust.id,
            'status': 'active',
            'items': {'data': [{'current_period_end': 9999999999}]},
        }
        with patch('billing.views.webhook._push_combined_discount'):
            _sync_subscription(fake_active)
        self.mock_retry.defer.assert_not_called()


class PortalSession(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('portaluser')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)

    def test_portal_session_created_for_real_customer(self):
        stripe_sub = create_stripe_subscription(self.cust.id)
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

    def test_portal_view_redirects_to_plans_without_stripe_subscription(self):
        from django.test import Client
        from django.urls import reverse
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            stripe_subscription_id=None,
            status='cancelled',
        )
        client = Client()
        client.force_login(self.user)
        r = client.get(reverse('billing:portal'))
        self.assertRedirects(r, reverse('billing:plans'), fetch_redirect_response=False)