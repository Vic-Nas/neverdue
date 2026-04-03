# accounts/views/google.py
import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from google_auth_oauthlib.flow import Flow
import google.auth.transport.requests
import google.oauth2.id_token

from accounts.models import User

logger = logging.getLogger(__name__)

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
]

_CLIENT_CONFIG = {
    "web": {
        "client_id":     settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
    }
}


def google_login(request):
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()

    flow = Flow.from_client_config(
        _CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=request.build_absolute_uri(reverse("accounts:google_callback")),
    )

    auth_kwargs = {
        "access_type": "offline",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    # Only force the consent screen when we need a new refresh token
    # (e.g. after the user revoked Google permissions).  Normal logins
    # skip it — Google reuses the existing grant silently.
    if request.session.pop("force_consent", False):
        auth_kwargs["prompt"] = "consent"

    authorization_url, state = flow.authorization_url(**auth_kwargs)
    request.session["oauth_state"] = state
    request.session["pkce_verifier"] = code_verifier
    return redirect(authorization_url)


def google_callback(request):
    state = request.GET.get("state")
    if not state or state != request.session.pop("oauth_state", None):
        messages.error(request, "Invalid OAuth state. Please try again.")
        return redirect("accounts:login")

    code = request.GET.get("code")
    if not code:
        messages.error(request, "Google login failed. Please try again.")
        return redirect("accounts:login")

    flow = Flow.from_client_config(
        _CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=request.build_absolute_uri(reverse("accounts:google_callback")),
        state=state,
    )
    code_verifier = request.session.pop("pkce_verifier", None)
    flow.fetch_token(code=code, code_verifier=code_verifier)
    creds = flow.credentials

    id_info = google.oauth2.id_token.verify_oauth2_token(
        creds.id_token,
        google.auth.transport.requests.Request(),
        settings.GOOGLE_CLIENT_ID,
    )
    google_id = id_info["sub"]
    email = id_info["email"]

    user, created = User.objects.get_or_create(
        google_id=google_id,
        defaults={"email": email, "username": email.split("@")[0]},
    )
    user.google_calendar_token = creds.token
    if creds.refresh_token:
        user.google_refresh_token = creds.refresh_token
    user.token_expiry = timezone.now() + timedelta(seconds=3600)
    user.save(update_fields=["google_calendar_token", "google_refresh_token", "token_expiry"])

    # No refresh token from Google AND none stored — we need to redo
    # the flow with prompt=consent so Google issues a new one.
    if not user.google_refresh_token:
        request.session["force_consent"] = True
        return redirect("accounts:google_login")

    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    from dashboard.gcal import register_gcal_watch
    register_gcal_watch(user)

    return redirect("accounts:username_pick") if created else redirect("dashboard:index")
