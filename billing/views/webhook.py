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
        if event['type'] in ('customer.subscription.created',
                             'customer.subscription.updated',
                             'customer.subscription.deleted'):
            _sync_subscription(event['data']['object'])
        elif event['type'] == 'checkout.session.completed':
            _handle_checkout_completed(event['data']['object'])
    except Exception as exc:
        logger.error('billing.webhook: handler failed | event=%s error=%s', event.get('type'), exc, exc_info=True)
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
        logger.info('_sync_subscription: synced customer=%s subscription=%s status=%s',
                     stripe_sub['customer'], stripe_sub['id'], stripe_sub['status'])

        if old_status != 'active' and sub.status == 'active':
            from emails.tasks import retry_jobs_after_plan_upgrade
            retry_jobs_after_plan_upgrade.defer(user_id=sub.user.pk)
            logger.info('_sync_subscription: triggered retry for user=%s on plan upgrade', sub.user.pk)
    except Exception:
        logger.exception('_sync_subscription: save failed for customer=%s subscription=%s',
                         stripe_sub['customer'], stripe_sub['id'])
