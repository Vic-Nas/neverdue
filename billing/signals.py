# billing/signals.py
"""
dj-stripe signal handlers.
All Stripe object syncing (Subscription status, Invoice data, etc.) is handled
automatically by dj-stripe. Only NeverDue business logic lives here.

Registered via BillingConfig.ready().
"""
import logging

import stripe
from django.conf import settings
from djstripe.signals import (
    WEBHOOK_EVENT_CALLBACK,
)

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _push_combined_discount(sub):
    """
    Compute the user's total discount and apply it as a single Stripe coupon
    (duration='once') on their subscription. Idempotent: safe to call multiple
    times; always deletes-then-recreates the nvd-auto-<pk> coupon.

    If percent is 0, removes any existing discount from the subscription.
    """
    from billing.models import compute_discount

    pct = compute_discount(sub.user)
    coupon_id = f'nvd-auto-{sub.user.pk}'

    try:
        try:
            stripe.Coupon.delete(coupon_id)
        except stripe.error.InvalidRequestError:
            pass  # didn't exist — fine

        if pct <= 0:
            stripe.Subscription.modify(sub.stripe_subscription_id, coupon='')
            logger.info('_push_combined_discount: removed discount | user=%s', sub.user.pk)
            return

        stripe.Coupon.create(id=coupon_id, percent_off=pct, duration='once')
        stripe.Subscription.modify(sub.stripe_subscription_id, coupon=coupon_id)
        logger.info(
            '_push_combined_discount: applied %s%% | user=%s sub=%s',
            pct, sub.user.pk, sub.stripe_subscription_id,
        )
    except stripe.error.StripeError:
        logger.exception(
            '_push_combined_discount: failed | user=%s', sub.user.pk
        )
        raise


def _get_local_sub_by_customer(customer_id, context=''):
    """Return local Subscription for a Stripe customer ID, or None."""
    from billing.models import Subscription
    try:
        return Subscription.objects.select_related('user').get(
            stripe_customer_id=customer_id
        )
    except Subscription.DoesNotExist:
        logger.warning('%s: no local subscription for customer=%s', context, customer_id)
        return None


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

def handle_customer_discount_created(event, **kwargs):
    """
    Fires when a PromotionCode is applied at Stripe checkout.

    Identifies the referrer via Subscription.referral_code, then creates a
    UserCoupon linking referrer and new subscriber.

    Guards:
    - Self-referral: strip Stripe discount, skip coupon creation.
    - Duplicate: UserCoupon linking these two already exists → skip.
    - Unknown code: not one of our referral codes → skip silently.
    Redemption limits are enforced by Stripe on the PromotionCode.
    """
    from billing.models import Subscription, UserCoupon

    obj = event.data['object']
    coupon_id = (obj.get('coupon') or {}).get('id', '')
    customer_id = obj.get('customer', '')

    if not coupon_id or not customer_id:
        return

    # Is this one of our referral codes?
    referrer_sub = (
        Subscription.objects
        .select_related('user')
        .filter(referral_code=coupon_id)
        .first()
    )
    if not referrer_sub:
        logger.debug(
            'handle_customer_discount_created: not a referral code | coupon=%s', coupon_id
        )
        return

    new_sub = _get_local_sub_by_customer(customer_id, 'handle_customer_discount_created')
    if not new_sub:
        return

    # Self-referral guard
    if referrer_sub.user.pk == new_sub.user.pk:
        logger.warning(
            'handle_customer_discount_created: self-referral blocked | user=%s', new_sub.user.pk
        )
        try:
            stripe.Customer.delete_discount(customer_id)
        except stripe.error.StripeError:
            logger.exception(
                'handle_customer_discount_created: failed to delete self-referral discount | '
                'customer=%s', customer_id
            )
        return

    # Duplicate guard
    existing = (
        UserCoupon.objects
        .filter(users=referrer_sub.user)
        .filter(users=new_sub.user)
        .first()
    )
    if existing:
        logger.info(
            'handle_customer_discount_created: duplicate coupon skipped | '
            'referrer=%s new_user=%s', referrer_sub.user.pk, new_sub.user.pk
        )
        return

    coupon = UserCoupon.objects.create(percent='12.50')
    coupon.users.set([referrer_sub.user, new_sub.user])
    logger.info(
        'handle_customer_discount_created: created UserCoupon id=%s | '
        'referrer=%s new_user=%s', coupon.pk, referrer_sub.user.pk, new_sub.user.pk
    )


def handle_invoice_paid(event, **kwargs):
    """
    On first real payment (subscription_create): push discount for all coupon
    partners of this user — both sides activate simultaneously.
    On renewal (subscription_cycle): push discount for this user only (safety net).
    """
    from billing.models import UserCoupon

    obj = event.data['object']
    customer_id = obj.get('customer', '')
    billing_reason = obj.get('billing_reason', '')

    sub = _get_local_sub_by_customer(customer_id, 'handle_invoice_paid')
    if not sub:
        return

    if billing_reason == 'subscription_create':
        # Push for this user and all their coupon partners
        partner_ids = set()
        for coupon in sub.user.coupons.prefetch_related('users'):
            for u in coupon.users.all():
                partner_ids.add(u.pk)
        partner_ids.discard(sub.user.pk)

        _push_combined_discount(sub)

        if partner_ids:
            from billing.models import Subscription
            for partner_sub in Subscription.objects.filter(
                user__pk__in=partner_ids,
                stripe_subscription_id__isnull=False,
            ).select_related('user'):
                try:
                    _push_combined_discount(partner_sub)
                except Exception:
                    logger.exception(
                        'handle_invoice_paid: failed pushing partner discount | user=%s',
                        partner_sub.user.pk,
                    )

    elif billing_reason == 'subscription_cycle':
        _push_combined_discount(sub)


def handle_invoice_upcoming(event, **kwargs):
    """
    Primary discount push path: ~1 hour before each billing cycle.
    Ensures the next invoice reflects the current discount state.
    """
    obj = event.data['object']
    customer_id = obj.get('customer', '')

    sub = _get_local_sub_by_customer(customer_id, 'handle_invoice_upcoming')
    if not sub or not sub.is_pro:
        return

    _push_combined_discount(sub)


def handle_subscription_updated(event, **kwargs):
    """
    Defers retry_jobs_after_plan_upgrade on any → active transition.
    dj-stripe handles syncing the local Subscription status automatically.
    """
    from billing.models import Subscription

    obj = event.data['object']
    previous = event.data.get('previous_attributes', {})
    customer_id = obj.get('customer', '')

    new_status = obj.get('status', '')
    old_status = previous.get('status', new_status)

    if old_status != 'active' and new_status == 'active':
        sub = _get_local_sub_by_customer(customer_id, 'handle_subscription_updated')
        if not sub:
            return
        from emails.tasks import retry_jobs_after_plan_upgrade
        retry_jobs_after_plan_upgrade.defer(user_id=sub.user.pk)
        logger.info(
            'handle_subscription_updated: deferred retry_jobs | user=%s', sub.user.pk
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _dispatch(event, **kwargs):
    """
    Single entry point for all dj-stripe webhook events we care about.
    Routes by event type. Unhandled types are silently ignored.
    """
    etype = event.type
    try:
        if etype == 'customer.discount.created':
            handle_customer_discount_created(event, **kwargs)
        elif etype == 'invoice.paid':
            handle_invoice_paid(event, **kwargs)
        elif etype == 'invoice.upcoming':
            handle_invoice_upcoming(event, **kwargs)
        elif etype == 'customer.subscription.updated':
            handle_subscription_updated(event, **kwargs)
    except Exception:
        logger.exception('billing.signals._dispatch: unhandled error | event=%s', etype)
        raise  # let dj-stripe return 500 so Stripe retries


WEBHOOK_EVENT_CALLBACK.connect(_dispatch)
