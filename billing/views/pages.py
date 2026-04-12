# billing/views/pages.py
import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from billing.models import Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)


def _get_or_create_customer(user):
    sub, created = Subscription.objects.get_or_create(
        user=user,
        defaults={
            'stripe_customer_id': stripe.Customer.create(
                email=user.email, name=user.username
            ).id
        },
    )
    if not created:
        try:
            stripe.Customer.retrieve(sub.stripe_customer_id)
        except stripe.error.InvalidRequestError:
            new_customer = stripe.Customer.create(email=user.email, name=user.username)
            sub.stripe_customer_id = new_customer.id
            sub.stripe_subscription_id = None
            sub.status = 'cancelled'
            sub.save(update_fields=['stripe_customer_id', 'stripe_subscription_id', 'status'])
    return sub, created


@login_required
def plans(request):
    from billing.models import compute_discount

    user = request.user
    sub = getattr(user, 'subscription', None)
    is_pro = user.is_pro
    show_referral = True

    discount = compute_discount(user) if is_pro else 0

    active_partners = 0
    if sub and sub.referral_coupon_id:
        active_partners = sub.referral_coupon.redemptions.filter(
            user__subscription__status='active'
        ).count()

    ctx = {
        'is_pro': is_pro,
        'show_referral': show_referral,
        'discount': discount,
        'active_partners': active_partners,
        'referral_code': sub.referral_code if sub else None,
    }
    try:
        return render(request, 'billing/membership.html', ctx)
    except Exception:
        return HttpResponse('Page unavailable.', status=500)


@login_required
@require_POST
def generate_referral_code(request):
    sub = getattr(request.user, 'subscription', None)
    if not sub:
        return JsonResponse({'error': 'No billing account found.'}, status=400)
    if sub.referral_coupon_id:
        return JsonResponse({'code': sub.referral_code})
    try:
        head = None if request.user.is_staff else request.user
        code = sub.generate_referral_code(head=head)
        return JsonResponse({'code': code})
    except Exception as exc:
        logger.error(
            'billing.generate_referral_code: failed | user_id=%s error=%s',
            request.user.pk, exc, exc_info=True,
        )
        return JsonResponse({'error': 'Could not generate code.'}, status=500)


def coupon_lookup(request):
    """
    Unauthenticated GET /billing/referral/lookup/?code=XYZ
    Returns JSON: head_active, head_label, redeemer_count.
    """
    from billing.models import Coupon

    code = request.GET.get('code', '').strip().upper()
    if not code:
        return JsonResponse({'error': 'No code provided.'}, status=400)

    try:
        coupon = Coupon.objects.select_related('head__subscription').get(code=code)
    except Coupon.DoesNotExist:
        return JsonResponse({'error': 'Code not found.'}, status=404)

    head = coupon.head
    head_active = (
        head is None or
        (hasattr(head, 'subscription') and head.subscription.status == 'active')
    )
    head_label = head.username if head else 'NeverDue'

    redeemer_count = coupon.redemptions.filter(
        user__subscription__status='active'
    ).count()

    return JsonResponse({
        'code': code,
        'head_active': head_active,
        'head_label': head_label,
        'redeemer_count': redeemer_count,
    })


@login_required
def checkout(request):
    """
    Redirect to Stripe Checkout with allow_promotion_codes=True.
    Users enter their coupon/referral code on Stripe's hosted page.
    Custom text clarifies that refunds are issued month-end.
    """
    try:
        sub, _ = _get_or_create_customer(request.user)
        session = stripe.checkout.Session.create(
            customer=sub.stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': settings.STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            subscription_data={'trial_period_days': 7},
            allow_promotion_codes=True,
            custom_text={
                'after_submit': {
                    'message': (
                        'Coupon discounts are applied as monthly refunds once '
                        'your subscription is confirmed active. '
                        'You will see the refund on your next statement.'
                    )
                }
            },
            success_url=request.build_absolute_uri('/billing/success/'),
            cancel_url=request.build_absolute_uri('/billing/cancel/'),
        )
        return redirect(session.url)
    except Exception as exc:
        logger.error(
            'billing.checkout: failed | user_id=%s error=%s',
            request.user.pk, exc, exc_info=True,
        )
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
        return redirect('billing:membership')
    if not sub.stripe_subscription_id:
        messages.info(
            request,
            'Your Pro access was granted manually and is not managed through Stripe. '
            'Contact support if you have questions about your account.',
        )
        return redirect('billing:membership')
    if not sub.stripe_customer_id or not sub.stripe_customer_id.startswith('cus_'):
        logger.error('billing.portal: invalid customer_id | user_id=%s', request.user.pk)
        messages.error(request, 'There is an issue with your billing account. Contact support.')
        return redirect('billing:membership')
    try:
        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=request.build_absolute_uri('/billing/membership/'),
        )
        return redirect(session.url)
    except stripe.error.InvalidRequestError as exc:
        if 'No such customer' in str(exc):
            sub.stripe_subscription_id = None
            sub.status = 'cancelled'
            sub.save(update_fields=['stripe_subscription_id', 'status'])
            messages.warning(request, 'Your billing account was reset. Please resubscribe.')
            return redirect('billing:membership')
        logger.exception('billing portal failed for user=%s', request.user.pk)
        messages.error(request, 'We could not open the billing portal right now. Please try again or contact support.')
        return redirect('billing:membership')
    except stripe.error.StripeError:
        logger.exception('billing portal failed for user=%s', request.user.pk)
        messages.error(
            request,
            'We could not open the billing portal right now. Please try again or contact support.',
        )
        return redirect('billing:membership')