# accounts/views/preferences.py
import logging
import zoneinfo

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

LANGUAGES = [
    'English', 'Français', 'Español', 'Deutsch',
    'Português', 'Italiano', '中文', '日本語', 'العربية',
]

GCAL_COLORS = [
    {'id': 1,  'name': 'Lavender',  'hex': '#7986cb'},
    {'id': 2,  'name': 'Sage',      'hex': '#33b679'},
    {'id': 3,  'name': 'Grape',     'hex': '#8e24aa'},
    {'id': 4,  'name': 'Flamingo',  'hex': '#e67c73'},
    {'id': 5,  'name': 'Banana',    'hex': '#f6c026'},
    {'id': 6,  'name': 'Tangerine', 'hex': '#f5511d'},
    {'id': 7,  'name': 'Peacock',   'hex': '#039be5'},
    {'id': 8,  'name': 'Graphite',  'hex': '#616161'},
    {'id': 9,  'name': 'Blueberry', 'hex': '#3f51b5'},
    {'id': 10, 'name': 'Basil',     'hex': '#0b8043'},
    {'id': 11, 'name': 'Tomato',    'hex': '#d60000'},
]

VALID_PRIORITY_COLOR_IDS = {c['id'] for c in GCAL_COLORS}
GCAL_COLOR_HEX = {str(c['id']): c['hex'].upper() for c in GCAL_COLORS}


def _parse_priority_color(post, field, default):
    try:
        value = int(post.get(field, default))
    except (ValueError, TypeError):
        return default
    return value if value in VALID_PRIORITY_COLOR_IDS else default


@login_required
def preferences(request):
    if request.method == 'POST':
        language = request.POST.get('language', 'English').strip()
        auto_delete = request.POST.get('auto_delete_past_events') == 'on'
        retention_days = request.POST.get('past_event_retention_days', '30').strip()
        delete_gcal = request.POST.get('delete_from_gcal_on_cleanup') == 'on'
        save_to_gcal = request.POST.get('save_to_gcal') == 'on'
        timezone_str = request.POST.get('timezone', 'UTC').strip()

        try:
            retention_days = max(1, int(retention_days))
        except (ValueError, TypeError):
            retention_days = 30

        if timezone_str not in zoneinfo.available_timezones():
            timezone_str = 'UTC'

        priority_color_low    = _parse_priority_color(request.POST, 'priority_color_low',    2)
        priority_color_medium = _parse_priority_color(request.POST, 'priority_color_medium',  5)
        priority_color_high   = _parse_priority_color(request.POST, 'priority_color_high',    6)
        priority_color_urgent = _parse_priority_color(request.POST, 'priority_color_urgent', 11)

        request.user.language = language
        request.user.auto_delete_past_events = auto_delete
        request.user.past_event_retention_days = retention_days
        request.user.delete_from_gcal_on_cleanup = delete_gcal
        request.user.save_to_gcal = save_to_gcal
        request.user.timezone = timezone_str
        request.user.timezone_auto_detected = False
        request.user.priority_color_low    = priority_color_low
        request.user.priority_color_medium = priority_color_medium
        request.user.priority_color_high   = priority_color_high
        request.user.priority_color_urgent = priority_color_urgent
        request.user.save(update_fields=[
            'language', 'auto_delete_past_events', 'past_event_retention_days',
            'delete_from_gcal_on_cleanup', 'save_to_gcal',
            'timezone', 'timezone_auto_detected',
            'priority_color_low', 'priority_color_medium',
            'priority_color_high', 'priority_color_urgent',
        ])
        messages.success(request, 'Preferences saved.')
        return redirect('accounts:preferences')

    user = request.user
    priority_levels = [
        {'label': 'Low',    'field': 'priority_color_low',    'current': user.priority_color_low},
        {'label': 'Medium', 'field': 'priority_color_medium', 'current': user.priority_color_medium},
        {'label': 'High',   'field': 'priority_color_high',   'current': user.priority_color_high},
        {'label': 'Urgent', 'field': 'priority_color_urgent', 'current': user.priority_color_urgent},
    ]

    return render(request, 'accounts/preferences.html', {
        'languages': LANGUAGES,
        'gcal_colors': GCAL_COLORS,
        'priority_levels': priority_levels,
    })


@login_required
def revoke_google(request):
    """Revoke Google permissions and disable GCal sync."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    from accounts.utils import revoke_google_token
    revoke_google_token(request.user)
    request.user.save_to_gcal = False
    request.user.save(update_fields=['save_to_gcal'])
    return JsonResponse({'ok': True})


@login_required
def check_username(request):
    """
    GET /accounts/preferences/username/check/?u=<username>
    Returns JSON availability status — used for live validation.
    """
    from accounts.models import User
    from emails.webhook import RESERVED_USERNAMES

    val = request.GET.get('u', '').strip().lower()

    if not val:
        return JsonResponse({'status': 'empty'})
    if len(val) < 3:
        return JsonResponse({'status': 'invalid', 'error': 'Too short — minimum 3 characters.'})
    if not val.replace('_', '').isalnum():
        return JsonResponse({'status': 'invalid', 'error': 'Only lowercase letters, numbers, and underscores allowed.'})
    if val == request.user.username:
        return JsonResponse({'status': 'invalid', 'error': 'That is already your username.'})
    if val in RESERVED_USERNAMES:
        return JsonResponse({'status': 'taken', 'error': 'That username is reserved.'})
    if User.objects.filter(username=val).exclude(pk=request.user.pk).exists():
        return JsonResponse({'status': 'taken', 'error': 'That username is already taken.'})

    return JsonResponse({'status': 'available'})

@login_required
@require_POST
def change_username(request):
    """
    POST /accounts/preferences/username/
    Charges CA$5 via Stripe off-session, then updates the username.
    Falls back to the old username if payment fails.
    Returns JSON so the preferences page can handle it inline.
    """
    from accounts.models import User
    from emails.webhook import RESERVED_USERNAMES

    new_username = request.POST.get('username', '').strip().lower()

    if not new_username:
        return JsonResponse({'error': 'Username cannot be empty.'}, status=400)
    if not new_username.replace('_', '').isalnum():
        return JsonResponse({'error': 'Only letters, numbers, and underscores allowed.'}, status=400)
    if new_username in RESERVED_USERNAMES:
        return JsonResponse({'error': 'That username is reserved.'}, status=400)
    if User.objects.filter(username=new_username).exclude(pk=request.user.pk).exists():
        return JsonResponse({'error': 'That username is already taken.'}, status=400)
    if new_username == request.user.username:
        return JsonResponse({'error': 'That is already your username.'}, status=400)

    sub = getattr(request.user, 'subscription', None)
    if not sub or not sub.stripe_customer_id:
        return JsonResponse({'error': 'No billing account found. Subscribe first.'}, status=400)

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        customer = stripe.Customer.retrieve(sub.stripe_customer_id)
        pm_id = customer.get('invoice_settings', {}).get('default_payment_method')
        if not pm_id:
            return JsonResponse({'error': 'No payment method on file.'}, status=400)

        stripe.PaymentIntent.create(
            amount=500,          # CA$5.00
            currency='cad',
            customer=sub.stripe_customer_id,
            payment_method=pm_id,
            payment_method_types=['card'],
            off_session=True,
            confirm=True,
            description='NeverDue username change',
        )
    except stripe.error.StripeError as exc:
        logger.error(
            'change_username: payment failed | user_id=%s error=%s',
            request.user.pk, exc,
        )
        return JsonResponse(
            {'error': 'Payment failed. Your username was not changed.'},
            status=402,
        )

    request.user.username = new_username
    request.user.save(update_fields=['username'])
    return JsonResponse({'ok': True, 'username': new_username})