# billing/views/webhook.py
import logging
from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from billing.models import Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)


@csrf_exempt
def webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    try:
        etype = event['type']
        obj = event['data']['object']
        if etype in ('customer.subscription.created',
                     'customer.subscription.updated',
                     'customer.subscription.deleted'):
            _sync_subscription(obj)
        elif etype == 'checkout.session.completed':
            _handle_checkout_completed(obj)
        elif etype == 'customer.discount.created':
            _handle_discount_created(obj)
        elif etype == 'invoice.upcoming':
            _handle_invoice_upcoming(obj)
    except Exception as exc:
        logger.error('billing.webhook: handler failed | event=%s error=%s',
                     event.get('type'), exc, exc_info=True)
        return HttpResponse(status=500)

    return HttpResponse(status=200)


def _handle_checkout_completed(session):
    subscription_id = session.get('subscription')
    discounts = session.get('discounts') or []
    if not subscription_id or not discounts:
        return
    sub_discounts = []
    for d in discounts:
        if d.get('promotion_code'):
            sub_discounts.append({'promotion_code': d['promotion_code']})
        elif d.get('coupon'):
            sub_discounts.append({'coupon': d['coupon']})
    if sub_discounts:
        try:
            stripe.Subscription.modify(subscription_id, discounts=sub_discounts)
        except stripe.error.StripeError as exc:
            logger.error('billing._handle_checkout_completed: failed | subscription_id=%s error=%s',
                         subscription_id, exc, exc_info=True)


def _handle_discount_created(discount_obj):
    """
    When a coupon is applied at Stripe checkout:
    - If it matches a referral code -> set referred_by on the user
    - If it matches a staff percent coupon -> create CouponRedemption
    - Otherwise (Stripe-native free month etc.) -> ignore
    """
    coupon_id = (discount_obj.get('coupon') or {}).get('id')
    customer_id = discount_obj.get('customer')
    if not coupon_id or not customer_id:
        return

    try:
        sub = Subscription.objects.select_related('user').get(
            stripe_customer_id=customer_id
        )
    except Subscription.DoesNotExist:
        logger.warning('_handle_discount_created: no subscription for customer=%s', customer_id)
        return

    # Case 1: referral code -- set referred_by on the user
    referrer_sub = Subscription.objects.select_related('user').filter(
        referral_code=coupon_id
    ).first()
    if referrer_sub:
        if not sub.user.referred_by_id:
            sub.user.referred_by = referrer_sub.user
            sub.user.save(update_fields=['referred_by'])
            logger.info('_handle_discount_created: set referred_by user=%s referrer=%s',
                        sub.user.pk, referrer_sub.user.pk)
        return

    # Case 2: staff percent coupon -- record redemption
    from billing.models import Coupon, CouponRedemption
    coupon = Coupon.objects.filter(code=coupon_id).first()
    if coupon:
        CouponRedemption.objects.get_or_create(user=sub.user, coupon=coupon)
        logger.info('_handle_discount_created: recorded redemption user=%s coupon=%s',
                    sub.user.pk, coupon_id)
        return

    # Case 3: Stripe-native coupon (free months etc.) -- nothing to do
    logger.debug('_handle_discount_created: ignoring Stripe-native coupon=%s', coupon_id)


def _handle_invoice_upcoming(invoice_obj):
    """
    Recompute and push the combined discount before each billing cycle.
    Stripe sends this ~1 hour before an invoice is finalized.
    """
    customer_id = invoice_obj.get('customer')
    if not customer_id:
        return
    try:
        sub = Subscription.objects.get(stripe_customer_id=customer_id)
        if sub.is_pro:
            _push_combined_discount(sub)
    except Subscription.DoesNotExist:
        logger.warning('_handle_invoice_upcoming: no subscription for customer=%s', customer_id)


def _sync_subscription(stripe_sub):
    try:
        sub = Subscription.objects.get(stripe_customer_id=stripe_sub['customer'])
    except Subscription.DoesNotExist:
        logger.warning('_sync_subscription: no subscription for customer=%s', stripe_sub['customer'])
        return

    old_status = sub.status
    sub.stripe_subscription_id = stripe_sub['id']
    sub.status = stripe_sub['status']
    try:
        period_end = stripe_sub['items']['data'][0]['current_period_end']
    except (KeyError, IndexError):
        period_end = None
    if period_end:
        sub.current_period_end = datetime.fromtimestamp(period_end, tz=dt_timezone.utc)

    try:
        sub.save()
        logger.info('_sync_subscription: synced customer=%s status=%s',
                    stripe_sub['customer'], stripe_sub['status'])
        if old_status != 'active' and sub.status == 'active':
            from emails.tasks import retry_jobs_after_plan_upgrade
            retry_jobs_after_plan_upgrade.defer(user_id=sub.user.pk)
            _push_combined_discount(sub)
    except Exception:
        logger.exception('_sync_subscription: save failed for customer=%s', stripe_sub['customer'])


def _push_combined_discount(sub):
    """
    Compute the user's total discount and apply it as a single Stripe coupon
    on their subscription. Uses duration='once' so it applies to the next
    invoice only -- recomputed fresh each billing cycle via invoice.upcoming.
    """
    from billing.discount import compute_discount
    pct = compute_discount(sub.user)

    coupon_id = f'nvd-auto-{sub.user.pk}'
    try:
        try:
            stripe.Coupon.delete(coupon_id)
        except stripe.error.InvalidRequestError:
            pass

        if pct <= 0:
            stripe.Subscription.modify(sub.stripe_subscription_id, discounts=[])
            return

        stripe.Coupon.create(id=coupon_id, percent_off=pct, duration='once')
        stripe.Subscription.modify(sub.stripe_subscription_id,
                                   discounts=[{'coupon': coupon_id}])
        logger.info('_push_combined_discount: applied %s%% to subscription=%s',
                    pct, sub.stripe_subscription_id)
    except stripe.error.StripeError as exc:
        logger.error('_push_combined_discount: failed | user=%s error=%s',
                     sub.user.pk, exc, exc_info=True)