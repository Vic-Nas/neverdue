# accounts/utils.py
import requests
from datetime import timedelta
from django.conf import settings
from django.utils import timezone


def get_valid_token(user):
    """
    Returns a valid Google access token for the user.
    Refreshes automatically if expired or about to expire (within 5 minutes).
    Raises ValueError if no refresh token is available.
    """
    buffer = timezone.now() + timedelta(minutes=5)

    if user.token_expiry and user.token_expiry > buffer:
        return user.google_calendar_token

    if not user.google_refresh_token:
        raise ValueError(f'No refresh token for user {user.pk}. Re-authentication required.')

    response = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': settings.GOOGLE_CLIENT_ID,
        'client_secret': settings.GOOGLE_CLIENT_SECRET,
        'refresh_token': user.google_refresh_token,
        'grant_type': 'refresh_token',
    })

    if response.status_code != 200:
        raise ValueError(f'Token refresh failed for user {user.pk}: {response.text}')

    data = response.json()
    user.google_calendar_token = data['access_token']
    user.token_expiry = timezone.now() + timedelta(seconds=data.get('expires_in', 3600))
    user.save(update_fields=['google_calendar_token', 'token_expiry'])

    return user.google_calendar_token