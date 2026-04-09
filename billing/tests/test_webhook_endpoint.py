# billing/tests/test_webhook_endpoint.py
"""
Webhook HTTP endpoint tests with properly signed payloads.
Checkout session creation test (real Stripe customer, real session URL returned).
"""
import json

import stripe
from django.test import Client, TestCase
from django.urls import reverse

from billing.models import Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, create_stripe_subscription,
    make_user, s, sign_stripe_webhook,
)


class WebhookSignatureEnforcement(TestCase):
    def setUp(self):
        self.client = Client()

    def test_no_signature_returns_400(self):
        r = self.client.post(
            reverse('billing:webhook'),
            data=b'{}',
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 400)

    def test_bad_signature_returns_400(self):
        r = self.client.post(
            reverse('billing:webhook'),
            data=b'{}',
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=badhash',
        )
        self.assertEqual(r.status_code, 400)

    def test_unknown_event_with_valid_signature_returns_200(self):
        payload = {'type': 'payment_intent.created', 'data': {'object': {}}}
        body, sig = sign_stripe_webhook(payload)
        r = self.client.post(
            reverse('billing:webhook'),
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sig,
        )
        self.assertEqual(r.status_code, 200)


class WebhookSubscriptionSync(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = make_user('webhookuser')
        self.cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', self.cust.id)
        self.local_sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status='cancelled',
        )

    def _post_event(self, event_type, obj):
        payload = {'type': event_type, 'data': {'object': obj}}
        body, sig = sign_stripe_webhook(payload)
        return self.client.post(
            reverse('billing:webhook'),
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sig,
        )

    def test_subscription_updated_event_syncs_status(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)

        r = self._post_event(
            'customer.subscription.updated',
            stripe_sub,
        )
        self.assertEqual(r.status_code, 200)
        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'trialing')

    def test_subscription_deleted_event_syncs_cancelled(self):
        stripe_sub = create_stripe_subscription(self.cust.id, trial_days=7)
        self.track('subscription', stripe_sub.id)
        cancelled = s().Subscription.retrieve(stripe_sub.id).cancel()

        r = self._post_event('customer.subscription.deleted', cancelled)
        self.assertEqual(r.status_code, 200)
        self.local_sub.refresh_from_db()
        self.assertEqual(self.local_sub.status, 'canceled')

    def test_discount_created_event_records_redemption(self):
        from billing.models import Coupon, CouponRedemption
        coupon = Coupon.objects.create(
            code='WF-WEBHOOK-10', percent=10, label='Webhook test'
        )
        coupon.sync_to_stripe()
        self.track('coupon', coupon.code)

        discount_obj = {
            'coupon': {'id': coupon.code},
            'customer': self.cust.id,
        }
        r = self._post_event('customer.discount.created', discount_obj)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(
            CouponRedemption.objects.filter(user=self.user, coupon=coupon).exists()
        )


class CheckoutSessionCreation(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = make_user('checkoutuser')
        self.client.force_login(self.user)

    def test_checkout_creates_customer_and_returns_stripe_url(self):
        r = self.client.get(reverse('billing:checkout'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('stripe.com', r['Location'])

        sub = Subscription.objects.get(user=self.user)
        self.assertTrue(sub.stripe_customer_id.startswith('cus_'))
        self.track('customer', sub.stripe_customer_id)

    def test_checkout_reuses_existing_customer(self):
        cust = create_stripe_customer(self.user.email, self.user.username)
        self.track('customer', cust.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status='cancelled',
        )
        r = self.client.get(reverse('billing:checkout'))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)
