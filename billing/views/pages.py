# billing/views/pages.py
import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from billing.discount import compute_discount, referral_summary
from billing.models import Coupon, Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)


@login_required
def plans(request):
    sub = getattr(request.user, 'subscription', None)
    is_pro = sub and sub.is_pro
    has_referral_code = sub and sub.referral_code

    show_referral = is_pro or has_referral_code

    discount = compute_discount(request.user) if is_pro else 0
    referred = referral_summary(request.user) if show_referral else []

    ctx = {
        'is_pro': is_pro,
        'show_referral': show_referral,
        'discount': discount,
        'referred': referred,
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
    if not sub or not sub.is_pro:
        return JsonResponse({'error': 'Pro subscription required.'}, status=403)
    if sub.referral_code:
        return JsonResponse({'code': sub.referral_code})
    try:
        code = sub.generate_referral_code()
        return JsonResponse({'code': code})
    except Exception as exc:
        logger.error('billing.generate_referral_code: failed | user_id=%s error=%s',
                     request.user.pk, exc, exc_info=True)
        return JsonResponse({'error': 'Could not generate code.'}, status=500)


@login_required
@require_POST
def checkout(request):
    """
    Receives an optional promo_code from the membership page form.
    Resolves it server-side before creating the Stripe session so Stripe
    shows the real benefit — "30 days free" for referrals, actual % for
    staff coupons — instead of the raw coupon face value.
    """
    raw_code = request.POST.get('promo_code', '').strip().upper()

    trial_days = 7          # default
    session_discounts = []  # [] means no discount passed to Stripe
    code_error = None

    referral_sub = None
    if raw_code:
        # Referral code?
        referral_sub = Subscription.objects.filter(referral_code=raw_code).first()
        if referral_sub and referral_sub.user != request.user:
            trial_days = 30
            # Do NOT set referred_by yet; wait until after Stripe session is created
        else:
            # Staff coupon?
            coupon = Coupon.objects.filter(code=raw_code).first()
            if coupon and coupon.is_redeemable():
                session_discounts = [{'coupon': raw_code}]
            else:
                code_error = raw_code

    if code_error:
        messages.error(request, f'"{code_error}" is not a valid promo code.')
        return redirect('billing:membership')

    try:
        sub, _ = _get_or_create_customer(request.user)

        session_kwargs = dict(
            customer=sub.stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': settings.STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            subscription_data={'trial_period_days': trial_days},
            # allow_promotion_codes intentionally omitted — codes are handled
            # server-side so Stripe always shows the real benefit to the user.
            success_url=request.build_absolute_uri('/billing/success/'),
            cancel_url=request.build_absolute_uri('/billing/cancel/'),
        )
        if session_discounts:
            session_kwargs['discounts'] = session_discounts

        session = stripe.checkout.Session.create(**session_kwargs)

        # Only commit referred_by once we know Stripe accepted the session
        if referral_sub and not request.user.referred_by_id:
            request.user.referred_by = referral_sub.user
            request.user.save(update_fields=['referred_by'])

        return redirect(session.url)

    except Exception as exc:
        logger.error('billing.checkout: failed | user_id=%s error=%s',
                     request.user.pk, exc, exc_info=True)
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
        messages.info(request,
            'Your Pro access was granted manually and is not managed through Stripe. '
            'Contact support if you have questions about your account.')
        return redirect('billing:membership')
    if not sub.stripe_customer_id or not sub.stripe_customer_id.startswith('cus_'):
        logger.error('billing.portal: invalid customer_id | user_id=%s', request.user.pk)
        messages.error(request, 'There is an issue with your billing account. Please contact support.')
        return redirect('billing:membership')
    try:
        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=request.build_absolute_uri('/billing/membership/'),
        )
        return redirect(session.url)
    except stripe.error.StripeError:
        logger.exception('billing portal failed for user=%s', request.user.pk)
        messages.error(request, 'We could not open the billing portal right now. Please try again or contact support.')
        return redirect('billing:membership')


def _get_or_create_customer(user):
    sub, created = Subscription.objects.get_or_create(
        user=user,
        defaults={'stripe_customer_id': stripe.Customer.create(email=user.email, name=user.username).id}
    )
    return sub, created
