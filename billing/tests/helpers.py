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


def make_coupon(head=None, percent='12.50', max_redemptions=12, code=None):
    from billing.models import Coupon
    import random
    import string
    if code is None:
        chars = string.ascii_uppercase + string.digits
        code = 'NVD-' + ''.join(random.choices(chars, k=5))
    return Coupon.objects.create(
        code=code,
        percent=Decimal(percent),
        max_redemptions=max_redemptions,
        head=head,
    )


def make_subscription(user, status='active', stripe_customer_id=None):
    from billing.models import Subscription
    if stripe_customer_id is None:
        stripe_customer_id = f'cus_test_{user.pk}'
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=stripe_customer_id,
        status=status,
    )


def make_redemption(coupon, user):
    from billing.models import CouponRedemption
    return CouponRedemption.objects.create(coupon=coupon, user=user)


def last_month_start():
    """Return the first day of the previous calendar month at 00:00 UTC."""
    now = timezone.now()
    first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day_prev = first_of_this - timezone.timedelta(days=1)
    return last_day_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def make_admin_sentinel():
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
    Create a djstripe Customer + Charge + Invoice row for the given user.
    Used in task tests — no live Stripe call needed.

    period_start: aware datetime for the billing period.
    amount_paid_cents: integer cents (e.g. 800 = $8.00).
    """
    import djstripe.models as djstripe
    from billing.models import Subscription

    sub = getattr(user, 'subscription', None)
    if not sub:
        raise ValueError(f'User {user.username} has no local Subscription row')

    customer_id = sub.stripe_customer_id
    invoice_id = f'in_test_{user.pk}_{int(period_start.timestamp())}'
    period_end = period_start + timezone.timedelta(days=30)

    customer_data = {
        'id': customer_id, 'object': 'customer', 'livemode': False,
        'created': int(period_start.timestamp()), 'metadata': {},
        'email': user.email, 'description': None, 'currency': 'cad',
        'delinquent': False, 'balance': 0, 'default_source': None,
        'invoice_settings': {'default_payment_method': None},
        'sources': {'object': 'list', 'data': [], 'has_more': False, 'url': ''},
        'subscriptions': {'object': 'list', 'data': [], 'has_more': False, 'url': ''},
        'tax_ids': {'object': 'list', 'data': [], 'has_more': False, 'url': ''},
    }
    dj_customer = djstripe.Customer.sync_from_stripe_data(customer_data)
    dj_customer.subscriber = user
    dj_customer.save()

    charge_data = {
        'id': charge_id, 'object': 'charge', 'livemode': False,
        'created': int(period_start.timestamp()), 'metadata': {},
        'customer': customer_id, 'amount': amount_paid_cents,
        'amount_captured': amount_paid_cents, 'amount_refunded': 0,
        'currency': 'cad', 'paid': True, 'captured': True,
        'refunded': False, 'status': 'succeeded', 'balance_transaction': None,
        'billing_details': {
            'address': {'city': None, 'country': None, 'line1': None,
                        'line2': None, 'postal_code': None, 'state': None},
            'email': None, 'name': None, 'phone': None,
        },
        'description': None, 'disputed': False,
        'failure_balance_transaction': None, 'failure_code': None,
        'failure_message': None, 'invoice': None, 'on_behalf_of': None,
        'order': None, 'outcome': None, 'payment_intent': None,
        'payment_method': None,
        'payment_method_details': {'type': 'card', 'card': {
            'amount_authorized': None, 'authorization_code': None,
            'brand': 'visa', 'checks': None, 'country': 'CA',
            'exp_month': 12, 'exp_year': 2030,
            'extended_authorization': None, 'fingerprint': None,
            'funding': 'credit', 'incremental_authorization': None,
            'installments': None, 'last4': '4242', 'mandate': None,
            'multicapture': None, 'network': None, 'network_token': None,
            'overcapture': None, 'three_d_secure': None, 'wallet': None,
        }},
        'radar_options': {}, 'receipt_email': None, 'receipt_number': None,
        'receipt_url': None,
        'refunds': {'object': 'list', 'data': [], 'has_more': False, 'url': ''},
        'review': None, 'shipping': None, 'source': None,
        'source_transfer': None, 'statement_descriptor': None,
        'statement_descriptor_suffix': None, 'transfer_data': None,
        'transfer_group': None,
    }
    djstripe.Charge.sync_from_stripe_data(charge_data)

    invoice_data = {
        'id': invoice_id, 'object': 'invoice', 'livemode': False,
        'created': int(period_start.timestamp()), 'metadata': {},
        'customer': customer_id, 'status': 'paid',
        'amount_paid': amount_paid_cents, 'amount_due': amount_paid_cents,
        'amount_remaining': 0, 'subtotal': amount_paid_cents,
        'total': amount_paid_cents, 'currency': 'cad',
        'period_start': int(period_start.timestamp()),
        'period_end': int(period_end.timestamp()),
        'billing_reason': 'subscription_cycle', 'charge': charge_id,
        'collection_method': 'send_invoice', 'description': None,
        'discount': None, 'due_date': None, 'ending_balance': None,
        'footer': None, 'hosted_invoice_url': None, 'invoice_pdf': None,
        'next_payment_attempt': None, 'number': None, 'paid': True,
        'receipt_number': None, 'starting_balance': 0,
        'statement_descriptor': None, 'subscription': None, 'tax': None,
        'webhooks_delivered_at': None, 'payment_intent': None,
        'default_payment_method': None, 'default_source': None,
        'default_tax_rates': [], 'discounts': [], 'lines': [],
        'account_country': None, 'account_name': None,
        'attempt_count': 1, 'attempted': True, 'auto_advance': False,
        'automatically_finalizes_at': None, 'custom_fields': None,
        'from_invoice': None, 'issuer': {'type': 'self'},
        'last_finalization_error': None, 'latest_revision': None,
        'on_behalf_of': None, 'rendering': None,
        'shipping_cost': None, 'shipping_details': None,
        'subtotal_excluding_tax': amount_paid_cents,
        'total_excluding_tax': amount_paid_cents,
        'total_discount_amounts': [], 'total_tax_amounts': [],
        'transfer_data': None,
    }
    return djstripe.Invoice.sync_from_stripe_data(invoice_data)


class BillingTestCase(TestCase):
    _cleanup = []

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
                    results = list(
                        s().PromotionCode.list(
                            code=obj_id, active=True, limit=1
                        ).auto_paging_iter()
                    )
                    if results:
                        s().PromotionCode.modify(results[0].id, active=False)
            except stripe.error.InvalidRequestError:
                pass

    def track(self, kind, obj_id):
        self._cleanup.append((kind, obj_id))
        return obj_id
