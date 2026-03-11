# accounts/views.py
import secrets
import requests
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import User


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
        return redirect('dashboard:index')

    return render(request, 'accounts/username_pick.html')