# billing/tests/helpers.py
"""
Real Stripe test-mode helpers.
- No payment sources — all subscriptions use trial_period_days to avoid immediate charge.
- BillingTestCase.tearDown cleans up all tracked Stripe objects.
- sign_stripe_webhook builds a valid Stripe-signed payload for webhook tests.
- make_djstripe_invoice creates a local djstripe Invoice row for task tests.
"""
import hashlib
import hmac
import json
import time
from datetime import timezone as dt_timezone
from decimal import Decimal

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

User = get_user_model()


def s():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def make_user(username, email=None):
    email = email or f'{username}@example.com'
    return User.objects.create_user(username=username, email=email, password='pw')


def make_admin_sentinel():
    """
    Return (or create) the admin sentinel user with a hardcoded active Subscription.
    """
    from billing.models import Subscription
    admin, _ = User.objects.get_or_create(
        username='admin',
        defaults={'email': 'admin@example.com'},
    )
    Subscription.objects.get_or_create(
        user=admin,
        defaults={
            'stripe_customer_id': 'cus_admin_sentinel',
            'status': 'active',
        },
    )
    return admin


def create_stripe_customer(email, username):
    cust = s().Customer.create(email=email, name=username)
    return cust


def create_stripe_subscription(customer_id, price_id=None, trial_days=7):
    """Trial-only — no payment method required."""
    return s().Subscription.create(
        customer=customer_id,
        items=[{'price': price_id or settings.STRIPE_PRICE_ID}],
        trial_period_days=trial_days,
        collection_method='send_invoice',
        days_until_due=30,
    )


def sign_stripe_webhook(payload_dict, secret=None):
    secret = secret or settings.STRIPE_WEBHOOK_SECRET
    body = json.dumps(payload_dict).encode()
    ts = str(int(time.time()))
    sig = hmac.HMAC(
        secret.encode(), f'{ts}.{body.decode()}'.encode(), hashlib.sha256
    ).hexdigest()
    return body, f't={ts},v1={sig}'


def make_djstripe_invoice(user, amount_paid_cents, period_start, charge_id='ch_test123'):
    """
    Create a djstripe Customer + Invoice row for the given user.
    Used in MonthlyRefundTask tests — no live Stripe call needed.

    period_start: aware datetime for the billing period.
    amount_paid_cents: integer cents (e.g. 800 = $8.00).
    """
    import djstripe.models as djstripe
    from billing.models import Subscription

    sub = getattr(user, 'subscription', None)
    if not sub:
        raise ValueError(f'User {user.username} has no local Subscription row')

    # Upsert a djstripe Customer
    dj_customer, _ = djstripe.Customer.objects.get_or_create(
        id=sub.stripe_customer_id,
        defaults={
            'subscriber': user,
            'livemode': False,
            'currency': 'usd',
            'delinquent': False,
            'djstripe_created': timezone.now(),
            'djstripe_updated': timezone.now(),
        },
    )

    invoice_id = f'in_test_{user.pk}_{int(period_start.timestamp())}'
    dj_invoice, _ = djstripe.Invoice.objects.update_or_create(
        id=invoice_id,
        defaults={
            'customer': dj_customer,
            'status': 'paid',
            'amount_paid': Decimal(amount_paid_cents),
            'period_start': period_start,
            'period_end': period_start + timezone.timedelta(days=30),
            'livemode': False,
            'currency': 'usd',
            'charge_id': charge_id,
            'djstripe_created': timezone.now(),
            'djstripe_updated': timezone.now(),
            'billing_reason': 'subscription_cycle',
            'subtotal': Decimal(amount_paid_cents),
            'total': Decimal(amount_paid_cents),
            'amount_due': Decimal(amount_paid_cents),
            'amount_remaining': Decimal(0),
        },
    )
    return dj_invoice


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
                    s().PromotionCode.modify(obj_id, active=False)
            except stripe.error.InvalidRequestError:
                pass

    def track(self, kind, obj_id):
        self._cleanup.append((kind, obj_id))
        return obj_id