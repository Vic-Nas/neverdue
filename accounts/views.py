# accounts/views.py
import secrets
import requests
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import json
import zoneinfo

from .models import User

LANGUAGES = [
    'English', 'Français', 'Español', 'Deutsch',
    'Português', 'Italiano', '中文', '日本語', 'العربية',
]

# Google Calendar color palette (colorId → display info).
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


def login(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    return render(request, 'accounts/login.html')


def logout(request):
    auth_logout(request)
    return redirect('accounts:login')


def google_login(request):
    state = secrets.token_urlsafe(32)
    request.session['oauth_state'] = state

    params = {
        'client_id': settings.GOOGLE_CLIENT_ID,
        'redirect_uri': request.build_absolute_uri(reverse('accounts:google_callback')),
        'response_type': 'code',
        'scope': ' '.join([
            'openid',
            'email',
            'profile',
            'https://www.googleapis.com/auth/calendar',
        ]),
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    }
    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + '&'.join(
        f'{k}={v}' for k, v in params.items()
    )
    return redirect(auth_url)


def google_callback(request):
    state = request.GET.get('state')
    if not state or state != request.session.pop('oauth_state', None):
        messages.error(request, 'Invalid OAuth state. Please try again.')
        return redirect('accounts:login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Google login failed. Please try again.')
        return redirect('accounts:login')

    token_response = requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': settings.GOOGLE_CLIENT_ID,
        'client_secret': settings.GOOGLE_CLIENT_SECRET,
        'redirect_uri': request.build_absolute_uri(reverse('accounts:google_callback')),
        'grant_type': 'authorization_code',
    })

    if token_response.status_code != 200:
        messages.error(request, 'Failed to authenticate with Google.')
        return redirect('accounts:login')

    token_data = token_response.json()
    access_token = token_data.get('access_token')
    refresh_token = token_data.get('refresh_token')

    userinfo_response = requests.get(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {access_token}'}
    )

    if userinfo_response.status_code != 200:
        messages.error(request, 'Failed to fetch your Google profile.')
        return redirect('accounts:login')

    userinfo = userinfo_response.json()
    google_id = userinfo.get('sub')
    email = userinfo.get('email')

    user, created = User.objects.get_or_create(
        google_id=google_id,
        defaults={
            'email': email,
            'username': email.split('@')[0],
        }
    )

    user.google_calendar_token = access_token
    if refresh_token:
        user.google_refresh_token = refresh_token
    user.token_expiry = timezone.now() + timedelta(seconds=token_data.get('expires_in', 3600))
    user.save(update_fields=['google_calendar_token', 'google_refresh_token', 'token_expiry'])

    auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    from dashboard.gcal import register_gcal_watch
    register_gcal_watch(user)

    if created:
        return redirect('accounts:username_pick')

    return redirect('dashboard:index')


def username_pick(request):
    if not request.user.is_authenticated:
        return redirect('accounts:login')

    if request.user.username and request.user.username != request.user.email.split('@')[0]:
        return redirect('dashboard:index')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()

        if not username:
            messages.error(request, 'Username cannot be empty.')
            return render(request, 'accounts/username_pick.html')

        if not username.replace('_', '').isalnum():
            messages.error(request, 'Only letters, numbers, and underscores allowed.')
            return render(request, 'accounts/username_pick.html')

        from emails.webhook import RESERVED_USERNAMES
        if username in RESERVED_USERNAMES:
            messages.error(request, 'That username is reserved. Please choose another.')
            return render(request, 'accounts/username_pick.html')

        if User.objects.filter(username=username).exclude(pk=request.user.pk).exists():
            messages.error(request, 'That username is already taken.')
            return render(request, 'accounts/username_pick.html')

        request.user.username = username
        request.user.save(update_fields=['username'])
        return redirect('billing:plans')

    return render(request, 'accounts/username_pick.html')


def _parse_priority_color(post, field, default):
    """Parse and validate a priority colorId from POST data."""
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
        request.user.timezone = timezone_str
        request.user.timezone_auto_detected = False
        request.user.priority_color_low    = priority_color_low
        request.user.priority_color_medium = priority_color_medium
        request.user.priority_color_high   = priority_color_high
        request.user.priority_color_urgent = priority_color_urgent
        request.user.save(update_fields=[
            'language',
            'auto_delete_past_events',
            'past_event_retention_days',
            'delete_from_gcal_on_cleanup',
            'timezone',
            'timezone_auto_detected',
            'priority_color_low',
            'priority_color_medium',
            'priority_color_high',
            'priority_color_urgent',
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


VALID_TIMEZONES = zoneinfo.available_timezones()


@login_required
@require_POST
def set_timezone_auto(request):
    """
    Called once by browser JS on first visit if timezone has never been set.
    Only updates if timezone_auto_detected is False AND timezone is still 'UTC'
    (meaning the user has never manually saved a preference).
    Silently ignored if user already has a real timezone set.
    """
    try:
        data = json.loads(request.body)
        tz = data.get('timezone', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'bad request'}, status=400)

    if tz not in VALID_TIMEZONES:
        return JsonResponse({'ok': False, 'error': 'unknown timezone'}, status=400)

    user = request.user
    if user.timezone == 'UTC' and not user.timezone_auto_detected:
        user.timezone = tz
        user.timezone_auto_detected = True
        user.save(update_fields=['timezone', 'timezone_auto_detected'])

    return JsonResponse({'ok': True, 'timezone': user.timezone})


@login_required
@require_POST
def set_timezone_manual(request):
    """
    Called from preferences form when user explicitly picks a timezone.
    Sets timezone_auto_detected = False so auto-detection never overwrites it again.
    """
    try:
        data = json.loads(request.body)
        tz = data.get('timezone', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'bad request'}, status=400)

    if tz not in VALID_TIMEZONES:
        return JsonResponse({'ok': False, 'error': 'unknown timezone'}, status=400)

    user = request.user
    user.timezone = tz
    user.timezone_auto_detected = False
    user.save(update_fields=['timezone', 'timezone_auto_detected'])

    return JsonResponse({'ok': True, 'timezone': user.timezone})
