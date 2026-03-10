# emails/webhook.py
import base64
import hashlib
import hmac
import time

from django.conf import settings


def verify_resend_signature(payload_bytes, headers):
    """
    Verify Resend webhook signature using Svix signing scheme.
    Returns True if valid, False otherwise.
    """
    secret = getattr(settings, 'RESEND_WEBHOOK_SECRET', '')
    if not secret:
        return False

    msg_id = headers.get('HTTP_SVIX_ID', '')
    msg_timestamp = headers.get('HTTP_SVIX_TIMESTAMP', '')
    msg_signature = headers.get('HTTP_SVIX_SIGNATURE', '')

    if not all([msg_id, msg_timestamp, msg_signature]):
        return False

    # Reject webhooks older than 5 minutes
    try:
        if abs(int(time.time()) - int(msg_timestamp)) > 300:
            return False
    except ValueError:
        return False

    # Strip 'whsec_' prefix and decode secret
    try:
        secret_bytes = base64.b64decode(secret.replace('whsec_', ''))
    except Exception:
        return False

    # Build signed content: id.timestamp.body
    signed_content = f'{msg_id}.{msg_timestamp}.{payload_bytes.decode()}'

    # Compute expected signature
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode(), hashlib.sha256).digest()
    ).decode()

    # Check against all provided signatures (space-separated, prefixed with 'v1,')
    for sig in msg_signature.split(' '):
        if sig.startswith('v1,') and hmac.compare_digest(sig[3:], expected):
            return True

    return False


def get_user_from_recipient(recipient):
    """
    Extract username from recipient address and return User or None.
    e.g. 'john@neverdue.ca' -> User(username='john')
    """
    from accounts.models import User

    try:
        username = recipient.split('@')[0].lower()
        return User.objects.get(username=username)
    except (User.DoesNotExist, IndexError):
        return None


def extract_email_text(payload):
    """
    Extract plain text body from Resend inbound webhook payload.
    Falls back to stripping HTML if plain text is absent.
    """
    data = payload.get('data', {})

    text = data.get('text', '').strip()
    if text:
        return text

    # Fallback: strip tags from HTML body
    html = data.get('html', '')
    if html:
        import re
        return re.sub(r'<[^>]+>', ' ', html).strip()

    return ''