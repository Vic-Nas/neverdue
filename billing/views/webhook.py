# billing/views/webhook.py
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from billing.models import Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY
# Pin to a stable pre-clover API version. The Stripe account is pinned to
# 2025-12-15.clover which removed subscription.discount (singular) in favour of
# subscription.discounts (array). Pinning here restores the schema that both this
# code and the test suite rely on (coupon= on Subscription.modify, expand=['discount']).
stripe.api_version = '2024-06-20'

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
    """
    After checkout:
    - If a referral code was used: extend B's trial to 30 days and strip the
      percent coupon off B's subscription (B gets free time, not a % discount).
      referred_by is set later via customer.discount.created -> _handle_discount_created.
    - If a non-referral promo/coupon was used: attach it to the subscription as before.
    """
    subscription_id = session.get('subscription')
    discounts = session.get('discounts') or []
    if not subscription_id:
        return

    referral_used = False
    non_referral_discounts = []

    for d in discounts:
        coupon_id = None
        if d.get('promotion_code'):
            try:
                pc = stripe.PromotionCode.retrieve(d['promotion_code'])
                coupon_id = pc['coupon']['id']
            except stripe.error.StripeError:
                pass
        elif d.get('coupon'):
            coupon_id = d['coupon']

        if coupon_id and Subscription.objects.filter(referral_code=coupon_id).exists():
            referral_used = True
        else:
            if d.get('promotion_code'):
                non_referral_discounts.append({'promotion_code': d['promotion_code']})
            elif d.get('coupon'):
                non_referral_discounts.append({'coupon': d['coupon']})

    try:
        if referral_used:
            trial_end = int(datetime.now(tz=dt_timezone.utc).timestamp()) + (30 * 24 * 60 * 60)
            stripe.Subscription.modify(
                subscription_id,
                trial_end=trial_end,
                proration_behavior='none',
                discounts=[],
            )
            logger.info('_handle_checkout_completed: extended trial 30d | subscription=%s',
                        subscription_id)
        if non_referral_discounts:
            stripe.Subscription.modify(subscription_id, discounts=non_referral_discounts)
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
            _shift_referrer_billing_anchor(sub)
    except Exception:
        logger.exception('_sync_subscription: save failed for customer=%s', stripe_sub['customer'])


def _shift_referrer_billing_anchor(sub):
    """
    When B (sub) transitions trialing -> active (first real payment confirmed),
    shift A's (referrer's) billing cycle anchor to max(A_period_end, B_period_end) + 1 day.
    Uses trial_end + proration_behavior='none' so A gets free extra days, no charge.
    Only moves forward, never back.
    """
    referrer = getattr(sub.user, 'referred_by', None)
    if not referrer:
        return
    referrer_sub = getattr(referrer, 'subscription', None)
    if not referrer_sub or not referrer_sub.stripe_subscription_id:
        return
    if not sub.current_period_end or not referrer_sub.current_period_end:
        return

    # Only shift if B's period ends after A's — i.e. A would bill before B next cycle
    if sub.current_period_end <= referrer_sub.current_period_end:
        return

    # Move A's next billing to one day after B's period ends
    new_anchor = sub.current_period_end + timedelta(days=1)
    new_anchor_ts = int(new_anchor.timestamp())

    try:
        stripe.Subscription.modify(
            referrer_sub.stripe_subscription_id,
            trial_end=new_anchor_ts,
            proration_behavior='none',
        )
        referrer_sub.current_period_end = new_anchor
        referrer_sub.save(update_fields=['current_period_end'])
        logger.info('_shift_referrer_billing_anchor: shifted referrer=%s to %s',
                    referrer.pk, new_anchor.date())
    except stripe.error.StripeError as exc:
        logger.error('_shift_referrer_billing_anchor: failed | referrer=%s error=%s',
                     referrer.pk, exc, exc_info=True)


def _push_combined_discount(sub):
    """
    Compute the user's total discount and apply it as a single Stripe coupon
    on their subscription. Uses duration='once' so it applies to the next
    invoice only — recomputed fresh each billing cycle via invoice.upcoming.
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
            stripe.Subscription.modify(sub.stripe_subscription_id, coupon='')
            return

        stripe.Coupon.create(id=coupon_id, percent_off=pct, duration='once')
        stripe.Subscription.modify(sub.stripe_subscription_id, coupon=coupon_id)
        logger.info('_push_combined_discount: applied %s%% to subscription=%s',
                    pct, sub.stripe_subscription_id)
    except stripe.error.StripeError as exc:
        logger.error('_push_combined_discount: failed | user=%s error=%s',
                     sub.user.pk, exc, exc_info=True)
        raise