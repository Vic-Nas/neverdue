# billing/tests/helpers.py
"""
Real Stripe test-mode helpers.
- Uses 4242424242424242 test card with attached PM so subscriptions actually activate.
- BillingTestCase.tearDown deletes all Stripe objects created during the test.
- sign_stripe_webhook builds a valid Stripe-signed payload for webhook tests.
"""
import hashlib
import hmac
import json
import time

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()
TEST_CARD = '4242424242424242'


def s():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def make_user(username, email=None):
    email = email or f'{username}@example.com'
    return User.objects.create_user(username=username, email=email, password='pw')


def create_payment_method():
    return s().PaymentMethod.create(
        type='card',
        card={'number': TEST_CARD, 'exp_month': 12, 'exp_year': 2030, 'cvc': '123'},
    )


def create_stripe_customer(email, username):
    pm = create_payment_method()
    cust = s().Customer.create(email=email, name=username)
    s().PaymentMethod.attach(pm.id, customer=cust.id)
    s().Customer.modify(cust.id, invoice_settings={'default_payment_method': pm.id})
    return cust


def create_stripe_subscription(customer_id, price_id=None, trial_days=None):
    kwargs = dict(
        customer=customer_id,
        items=[{'price': price_id or settings.STRIPE_PRICE_ID}],
        expand=['latest_invoice.payment_intent'],
    )
    if trial_days:
        kwargs['trial_period_days'] = trial_days
    return s().Subscription.create(**kwargs)


def sign_stripe_webhook(payload_dict, secret=None):
    secret = secret or settings.STRIPE_WEBHOOK_SECRET
    body = json.dumps(payload_dict).encode()
    ts = str(int(time.time()))
    sig = hmac.HMAC(secret.encode(), f'{ts}.{body.decode()}'.encode(), hashlib.sha256).hexdigest()
    return body, f't={ts},v1={sig}'


class BillingTestCase(TestCase):
    _cleanup = []  # list of (type, id)

    def setUp(self):
        super().setUp()
        stripe.api_key = settings.STRIPE_SECRET_KEY
        self.__class__._cleanup = []

    def tearDown(self):
        super().tearDown()
        for kind, obj_id in reversed(self._cleanup):
            try:
                if kind == 'subscription':
                    s().Subscription.retrieve(obj_id).cancel()
                elif kind == 'customer':
                    s().Customer.delete(obj_id)
                elif kind == 'coupon':
                    s().Coupon.delete(obj_id)
                elif kind == 'promotion_code':
                    pass  # cannot delete, deactivate only
            except stripe.error.InvalidRequestError:
                pass

    def track(self, kind, obj_id):
        self._cleanup.append((kind, obj_id))
        return obj_id
