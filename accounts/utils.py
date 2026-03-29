# accounts/utils.py
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


def get_valid_token(user) -> str:
    """
    Return a valid Google access token for the user.
    Refreshes automatically via google-auth if expired or within 5 minutes of expiry.
    Raises ValueError if the token cannot be refreshed.
    """
    if not user.google_refresh_token:
        raise ValueError(f"No refresh token for user {user.pk}. Re-authentication required.")

    creds = Credentials(
        token=user.google_calendar_token,
        refresh_token=user.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )

    buffer = timezone.now() + timedelta(minutes=5)
    needs_refresh = (
        not creds.valid
        or not user.token_expiry
        or user.token_expiry <= buffer
    )

    if needs_refresh:
        try:
            creds.refresh(Request())
        except Exception as exc:
            logger.error("accounts.get_valid_token: refresh failed | user=%s error=%s", user.pk, exc)
            raise ValueError(f"Token refresh failed for user {user.pk}: {exc}") from exc

        user.google_calendar_token = creds.token
        user.token_expiry = timezone.now() + timedelta(seconds=3600)
        user.save(update_fields=["google_calendar_token", "token_expiry"])
        if settings.DEBUG:
            logger.debug("accounts.get_valid_token: refreshed | user=%s", user.pk)

    return user.google_calendar_token