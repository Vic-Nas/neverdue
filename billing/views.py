# billing/views.py
import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from .models import Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY


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
            success_url=request.build_absolute_uri('/billing/success/'),
            cancel_url=request.build_absolute_uri('/billing/cancel/'),
        )
        return redirect(session.url)
    except Exception:
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
    try:
        sub = getattr(request.user, 'subscription', None)
        if not sub:
            return redirect('billing:plans')
        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=request.build_absolute_uri('/'),
        )
        return redirect(session.url)
    except Exception:
        return HttpResponse('Could not open billing portal.', status=500)


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

    if event['type'] == 'customer.subscription.updated':
        _sync_subscription(event['data']['object'])
    elif event['type'] == 'customer.subscription.deleted':
        _sync_subscription(event['data']['object'])

    return HttpResponse(status=200)


def _get_or_create_customer(user):
    sub, created = Subscription.objects.get_or_create(
        user=user,
        defaults={'stripe_customer_id': _create_stripe_customer(user)}
    )
    return sub, created


def _create_stripe_customer(user):
    customer = stripe.Customer.create(email=user.email, name=user.username)
    return customer.id


def _sync_subscription(stripe_sub):
    try:
        sub = Subscription.objects.get(stripe_subscription_id=stripe_sub['id'])
        sub.status = stripe_sub['status']
        sub.save()
    except Subscription.DoesNotExist:
        pass