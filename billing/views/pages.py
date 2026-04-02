# billing/views/pages.py
import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from billing.models import Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)


@login_required
def plans(request):
    try:
        return render(request, 'billing/plans.html')
    except Exception:
        return HttpResponse('Plans unavailable.', status=500)


@login_required
def checkout(request):
    try:
        customer, _ = _get_or_create_customer(request.user)
        session = stripe.checkout.Session.create(
            customer=customer.stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': settings.STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            subscription_data={'trial_period_days': 7},
            allow_promotion_codes=True,
            success_url=request.build_absolute_uri('/billing/success/'),
            cancel_url=request.build_absolute_uri('/billing/cancel/'),
        )
        return redirect(session.url)
    except Exception as exc:
        logger.error('billing.checkout: failed | user_id=%s error=%s', request.user.pk, exc, exc_info=True)
        return HttpResponse('Checkout unavailable.', status=500)


@login_required
def success(request):
    try:
        return render(request, 'billing/success.html')
    except Exception:
        return HttpResponse('Could not load confirmation.', status=500)


@login_required
def cancel(request):
    try:
        return render(request, 'billing/cancel.html')
    except Exception:
        return HttpResponse('Could not load page.', status=500)


@login_required
def portal(request):
    sub = getattr(request.user, 'subscription', None)

    if not sub:
        return redirect('billing:plans')

    if not sub.stripe_subscription_id:
        messages.info(
            request,
            'Your Pro access was granted manually and is not managed through Stripe. '
            'Contact support if you have questions about your account.',
        )
        return redirect('billing:plans')

    if not sub.stripe_customer_id or not sub.stripe_customer_id.startswith('cus_'):
        logger.error(
            'billing.portal: invalid customer_id | user_id=%s subscription_id=%s customer_id=%s',
            request.user.pk, sub.stripe_subscription_id, sub.stripe_customer_id,
        )
        messages.error(request, 'There is an issue with your billing account. Please contact support.')
        return redirect('billing:plans')

    try:
        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=request.build_absolute_uri('/billing/plans/'),
        )
        return redirect(session.url)
    except stripe.error.StripeError:
        logger.exception('billing portal failed for user=%s customer=%s', request.user.pk, sub.stripe_customer_id)
        messages.error(request, 'We could not open the billing portal right now. Please try again or contact support.')
        return redirect('billing:plans')


def _get_or_create_customer(user):
    sub, created = Subscription.objects.get_or_create(
        user=user,
        defaults={'stripe_customer_id': stripe.Customer.create(email=user.email, name=user.username).id}
    )
    return sub, created
