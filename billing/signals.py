# billing/signals.py
"""
dj-stripe signal handlers.
All Stripe object syncing is handled automatically by dj-stripe.
Only NeverDue business logic lives here.

Registered via BillingConfig.ready().
"""
import logging

import stripe
from django.conf import settings
from djstripe.signals import WEBHOOK_SIGNALS
from emails.tasks import retry_jobs_after_plan_upgrade

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sub_by_customer(customer_id, ctx=''):
    from billing.models import Subscription
    try:
        return Subscription.objects.select_related('user').get(
            stripe_customer_id=customer_id
        )
    except Subscription.DoesNotExist:
        logger.warning('%s: no local subscription for customer=%s', ctx, customer_id)
        return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_checkout_session_completed(event, **kwargs):
    """
    Fires when a Stripe Checkout session completes successfully.

    If the user had a coupon code stored in their Django session we can't
    access it here (no request context), so instead we rely on the
    checkout session's metadata. The coupon code is written to metadata
    by the checkout view when a pending_coupon_code is present in the
    user's session.

    Guards:
    - No metadata code → skip (user went straight to checkout, no coupon).
    - Code not found locally → skip.
    - Self-referral → skip.
    - Duplicate redemption → skip.
    """
    from billing.models import Coupon, CouponRedemption

    obj = event["data"]['object']
    customer_id = obj.get('customer', '')
    metadata = obj.get('metadata') or {}
    code = metadata.get('coupon_code', '').strip().upper()

    if not code or not customer_id:
        return

    try:
        coupon = Coupon.objects.select_related('head').get(code=code)
    except Coupon.DoesNotExist:
        logger.debug('handle_checkout_session_completed: unknown code=%s', code)
        return

    sub = _sub_by_customer(customer_id, 'handle_checkout_session_completed')
    if not sub:
        return

    new_user = sub.user

    if coupon.head_id and coupon.head_id == new_user.pk:
        logger.warning(
            'handle_checkout_session_completed: self-referral blocked | user=%s code=%s',
            new_user.pk, code,
        )
        return

    if CouponRedemption.objects.filter(coupon=coupon, user=new_user).exists():
        logger.info(
            'handle_checkout_session_completed: duplicate redemption skipped | '
            'user=%s code=%s', new_user.pk, code,
        )
        return

    CouponRedemption.objects.create(coupon=coupon, user=new_user)
    logger.info(
        'handle_checkout_session_completed: CouponRedemption created | '
        'user=%s code=%s coupon=%s', new_user.pk, code, coupon.pk,
    )


def handle_subscription_updated(event, **kwargs):
    """Defers retry_jobs_after_plan_upgrade on any → active transition."""
    from billing.models import Subscription  # noqa: F401 — dj-stripe syncs status

    obj = event["data"]['object']
    previous = event["data"].get('previous_attributes', {})
    customer_id = obj.get('customer', '')
    new_status = obj.get('status', '')
    old_status = previous.get('status', new_status)

    if old_status != 'active' and new_status == 'active':
        sub = _sub_by_customer(customer_id, 'handle_subscription_updated')
        if sub:
            retry_jobs_after_plan_upgrade.defer(user_id=sub.user.pk)
            logger.info('handle_subscription_updated: deferred retry | user=%s', sub.user.pk)


def handle_subscription_cancelled(event, **kwargs):
    """
    Fires on customer.subscription.deleted.

    Deletes all CouponRedemption rows for the cancelled user, freeing their
    slot(s). If they resubscribe they enter a fresh code at checkout.
    Coupons themselves (including referral coupons) are not deleted — the
    code stays live on Stripe for other redeemers.
    """
    from billing.models import CouponRedemption

    obj = event["data"]['object']
    customer_id = obj.get('customer', '')

    sub = _sub_by_customer(customer_id, 'handle_subscription_cancelled')
    if not sub:
        return

    deleted, _ = CouponRedemption.objects.filter(user=sub.user).delete()
    logger.info(
        'handle_subscription_cancelled: deleted %d CouponRedemption rows | user=%s',
        deleted, sub.user.pk,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _wrap(handler):
    def receiver(sender, event, **kwargs):
        try:
            handler(event, **kwargs)
        except Exception:
            logger.exception('billing.signals: unhandled error | event=%s', event.type)
            raise
    return receiver


WEBHOOK_SIGNALS['checkout.session.completed'].connect(_wrap(handle_checkout_session_completed))
WEBHOOK_SIGNALS['customer.subscription.updated'].connect(_wrap(handle_subscription_updated))
WEBHOOK_SIGNALS['customer.subscription.deleted'].connect(_wrap(handle_subscription_cancelled))