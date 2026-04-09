# billing/tests/helpers.py
"""
Shared utilities for billing tests.
All tests run against Stripe test-mode (sk_test_…) from dev.env.
DEBUG=True loads dev.env automatically.
"""
import os
import time
import json
import hashlib
import hmac

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


def stripe_client():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def make_user(username="testuser", email=None, **kwargs):
    email = email or f"{username}@test.example"
    return User.objects.create_user(
        username=username,
        email=email,
        password="pw",
        **kwargs,
    )


def make_stripe_customer(email="test@example.com"):
    s = stripe_client()
    return s.Customer.create(email=email, metadata={"test": "1"})


def make_stripe_subscription(customer_id, price_id=None, trial=False):
    s = stripe_client()
    price_id = price_id or settings.STRIPE_PRICE_ID
    kwargs = dict(
        customer=customer_id,
        items=[{"price": price_id}],
        payment_behavior="default_incomplete",
        expand=["latest_invoice.payment_intent"],
    )
    if trial:
        kwargs["trial_period_days"] = 7
    return s.Subscription.create(**kwargs)


def make_webhook_payload(event_type, data_obj):
    return json.dumps({"type": event_type, "data": {"object": data_obj}}).encode()


def sign_webhook(payload_bytes, secret):
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload_bytes.decode()}"
    sig = hmac.HMAC(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={sig}"


class BillingTestCase(TestCase):
    """Base class: sets stripe key, provides cleanup list for Stripe objects."""

    _stripe_cleanup = []  # list of (type, id) tuples

    def setUp(self):
        super().setUp()
        stripe.api_key = settings.STRIPE_SECRET_KEY
        self.__class__._stripe_cleanup = []

    def tearDown(self):
        super().tearDown()
        s = stripe_client()
        for kind, obj_id in reversed(self._stripe_cleanup):
            try:
                if kind == "customer":
                    s.Customer.delete(obj_id)
                elif kind == "coupon":
                    s.Coupon.delete(obj_id)
                elif kind == "subscription":
                    s.Subscription.retrieve(obj_id).cancel()
            except stripe.error.InvalidRequestError:
                pass

    def track(self, kind, obj_id):
        self._stripe_cleanup.append((kind, obj_id))
        return obj_id
