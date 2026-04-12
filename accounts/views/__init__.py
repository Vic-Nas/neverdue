# accounts/views/__init__.py
from .auth import login, logout
from .google import google_login, google_callback, SCOPES
from .preferences import (
    preferences, revoke_google, change_username,
    GCAL_COLORS, GCAL_COLOR_HEX, LANGUAGES,
    VALID_PRIORITY_COLOR_IDS,
)
from .timezone import set_timezone_auto, set_timezone_manual
