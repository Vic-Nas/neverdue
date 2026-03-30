# accounts/utils.py
import logging
import requests as http_requests
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

def revoke_google_token(user) -> None:
    """
    Revoke the user's Google OAuth token via Google's revocation endpoint.
    Clears stored tokens from the user record regardless of whether the
    revocation call succeeds (to avoid leaving stale tokens on re-auth failure).
    Should be called before logging the user out when revoke_google_on_logout is True.
    """
    token = user.google_calendar_token or user.google_refresh_token
    if token:
        try:
            resp = http_requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5,
            )
            if resp.status_code == 200:
                logger.info("accounts.revoke_google_token: revoked | user=%s", user.pk)
            else:
                logger.warning(
                    "accounts.revoke_google_token: unexpected status %s | user=%s",
                    resp.status_code, user.pk,
                )
        except Exception as exc:
            logger.error("accounts.revoke_google_token: request failed | user=%s error=%s", user.pk, exc)

    # Clear tokens locally regardless of API outcome
    user.google_calendar_token = None
    user.google_refresh_token = None
    user.token_expiry = None
    user.save(update_fields=["google_calendar_token", "google_refresh_token", "token_expiry"])