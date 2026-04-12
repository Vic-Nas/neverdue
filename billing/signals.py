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
from djstripe.signals import WEBHOOK_SIGNALS
from emails.tasks import retry_jobs_after_plan_upgrade

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

_REFERRAL_COUPON_PREFIX = 'nvd-referral-'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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

    Identifies the referrer by parsing the Stripe Coupon ID. Referral coupons
    are named 'nvd-referral-<user_pk>' by generate_referral_code(). The
    human-readable NVD-XXXXX code lives on the PromotionCode object and is NOT
    present in the discount event — only the underlying coupon.id is sent.

    Guards:
    - Self-referral: strip Stripe discount, skip coupon creation.
    - Duplicate: UserCoupon linking these two already exists -> skip.
    - Unknown coupon: not one of our referral coupon IDs -> skip silently.
    Redemption limits are enforced by Stripe on the PromotionCode.
    """
    from billing.models import Subscription, UserCoupon

    obj = event.data['object']
    coupon_id = (obj.get('coupon') or {}).get('id', '')
    customer_id = obj.get('customer', '')

    if not coupon_id or not customer_id:
        return

    # Is this one of our per-user referral coupons? ID format: 'nvd-referral-<pk>'
    if not coupon_id.startswith(_REFERRAL_COUPON_PREFIX):
        logger.debug(
            'handle_customer_discount_created: not a referral coupon | coupon=%s', coupon_id
        )
        return

    try:
        referrer_user_pk = int(coupon_id[len(_REFERRAL_COUPON_PREFIX):])
    except ValueError:
        logger.debug(
            'handle_customer_discount_created: malformed referral coupon id | coupon=%s', coupon_id
        )
        return

    referrer_sub = (
        Subscription.objects
        .select_related('user')
        .filter(user__pk=referrer_user_pk)
        .first()
    )
    if not referrer_sub:
        logger.debug(
            'handle_customer_discount_created: no subscription for referrer_pk=%s',
            referrer_user_pk,
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


def handle_subscription_updated(event, **kwargs):
    """
    Defers retry_jobs_after_plan_upgrade on any -> active transition.
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
        retry_jobs_after_plan_upgrade.defer(user_id=sub.user.pk)
        logger.info(
            'handle_subscription_updated: deferred retry_jobs | user=%s', sub.user.pk
        )


def handle_subscription_cancelled(event, **kwargs):
    """
    Fires when a subscription is deleted (fully cancelled) in Stripe.

    Deletes all UserCoupon rows the user is on, freeing their slot.
    If they resubscribe later they must enter a new referral code.

    The admin sentinel is never a Stripe customer so this handler will
    never fire for sentinel rows — those are managed by staff directly.
    """
    from billing.models import UserCoupon

    obj = event.data['object']
    customer_id = obj.get('customer', '')

    sub = _get_local_sub_by_customer(customer_id, 'handle_subscription_cancelled')
    if not sub:
        return

    deleted_count, _ = UserCoupon.objects.filter(users=sub.user).delete()
    logger.info(
        'handle_subscription_cancelled: deleted %d UserCoupon rows | user=%s',
        deleted_count, sub.user.pk,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
# WEBHOOK_SIGNALS is a dict of {event_type: django.dispatch.Signal}.
# Each signal sends (sender, event, **kwargs). We wrap each handler to catch
# and re-raise so dj-stripe returns 500 and Stripe retries on failure.

def _wrap(handler):
    def receiver(sender, event, **kwargs):
        try:
            handler(event, **kwargs)
        except Exception:
            logger.exception(
                'billing.signals: unhandled error | event=%s', event.type
            )
            raise
    return receiver


WEBHOOK_SIGNALS['customer.discount.created'].connect(
    _wrap(handle_customer_discount_created)
)
WEBHOOK_SIGNALS['customer.subscription.updated'].connect(
    _wrap(handle_subscription_updated)
)
WEBHOOK_SIGNALS['customer.subscription.deleted'].connect(
    _wrap(handle_subscription_cancelled)
)