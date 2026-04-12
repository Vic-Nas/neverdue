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

def handle_customer_discount_created(event, **kwargs):
    """
    Fires when a user enters a PromotionCode at Stripe checkout.

    Looks up the Coupon by code (from the PromotionCode object on the event).
    Creates a CouponRedemption row linking coupon → new subscriber.

    Guards:
    - Code not found locally → skip silently (not our coupon).
    - Head trying to redeem their own referral coupon → strip Stripe discount.
    - Duplicate redemption (user already on this coupon) → skip.
    """
    from billing.models import Coupon, CouponRedemption

    obj = event.data['object']
    customer_id = obj.get('customer', '')
    # The promotion_code object is nested; fall back to coupon.id for referral lookup
    promo = obj.get('promotion_code') or {}
    code = promo.get('code', '') if isinstance(promo, dict) else ''

    # If promotion_code isn't expanded, derive code from coupon id (nvd-<code>)
    if not code:
        coupon_id = (obj.get('coupon') or {}).get('id', '')
        if coupon_id.startswith('nvd-'):
            code = coupon_id[4:].upper()

    if not code or not customer_id:
        return

    try:
        coupon = Coupon.objects.select_related('head').get(code=code.upper())
    except Coupon.DoesNotExist:
        logger.debug('handle_customer_discount_created: unknown code=%s', code)
        return

    new_sub = _sub_by_customer(customer_id, 'handle_customer_discount_created')
    if not new_sub:
        return

    new_user = new_sub.user

    # Self-referral guard: head cannot redeem their own referral coupon
    if coupon.head_id and coupon.head_id == new_user.pk:
        logger.warning(
            'handle_customer_discount_created: self-referral blocked | user=%s code=%s',
            new_user.pk, code,
        )
        try:
            stripe.Customer.delete_discount(customer_id)
        except stripe.error.StripeError:
            logger.exception(
                'handle_customer_discount_created: failed to delete self-referral discount | '
                'customer=%s', customer_id,
            )
        return

    # Duplicate guard
    if CouponRedemption.objects.filter(coupon=coupon, user=new_user).exists():
        logger.info(
            'handle_customer_discount_created: duplicate redemption skipped | '
            'user=%s code=%s', new_user.pk, code,
        )
        return

    CouponRedemption.objects.create(coupon=coupon, user=new_user)
    logger.info(
        'handle_customer_discount_created: CouponRedemption created | '
        'user=%s code=%s coupon=%s', new_user.pk, code, coupon.pk,
    )


def handle_subscription_updated(event, **kwargs):
    """Defers retry_jobs_after_plan_upgrade on any → active transition."""
    from billing.models import Subscription  # noqa: F401 — dj-stripe syncs status

    obj = event.data['object']
    previous = event.data.get('previous_attributes', {})
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

    obj = event.data['object']
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


WEBHOOK_SIGNALS['customer.discount.created'].connect(_wrap(handle_customer_discount_created))
WEBHOOK_SIGNALS['customer.subscription.updated'].connect(_wrap(handle_subscription_updated))
WEBHOOK_SIGNALS['customer.subscription.deleted'].connect(_wrap(handle_subscription_cancelled))