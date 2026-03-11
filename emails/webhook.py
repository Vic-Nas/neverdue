# emails/webhook.py
import base64
import hashlib
import hmac
import time

from django.conf import settings

SUPPORTED_ATTACHMENT_TYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
    'text/plain',
}


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

    try:
        if abs(int(time.time()) - int(msg_timestamp)) > 300:
            return False
    except ValueError:
        return False

    try:
        secret_bytes = base64.b64decode(secret.replace('whsec_', ''))
    except Exception:
        return False

    signed_content = f'{msg_id}.{msg_timestamp}.{payload_bytes.decode()}'

    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode(), hashlib.sha256).digest()
    ).decode()

    for sig in msg_signature.split(' '):
        if sig.startswith('v1,') and hmac.compare_digest(sig[3:], expected):
            return True

    return False


def extract_email_text(payload):
    """
    Extract plain text body from Resend inbound webhook payload.
    Falls back to stripping HTML if plain text is absent.
    """
    data = payload.get('data', {})

    text = data.get('text', '').strip()
    if text:
        return text

    html = data.get('html', '')
    if html:
        import re
        return re.sub(r'<[^>]+>', ' ', html).strip()

    return ''


def extract_attachments(payload):
    """
    Extract supported attachments from Resend inbound webhook payload.
    Returns list of (bytes, media_type) tuples.
    """
    data = payload.get('data', {})
    attachments = data.get('attachments', [])
    result = []

    for attachment in attachments:
        content_type = attachment.get('contentType', '').split(';')[0].strip().lower()
        if content_type not in SUPPORTED_ATTACHMENT_TYPES:
            continue
        content = attachment.get('content', '')
        if not content:
            continue
        try:
            file_bytes = base64.b64decode(content)
            result.append((file_bytes, content_type))
        except Exception:
            continue

    return result


RESERVED_USERNAMES = {
    'admin', 'status', 'support', 'help', 'billing', 'api', 'www',
    'noreply', 'no-reply', 'mail', 'email', 'info', 'hello', 'contact',
    'abuse', 'security', 'postmaster', 'hostmaster', 'webmaster',
}


def get_user_from_recipient(recipient):
    """
    Extract username from recipient address and return User or None.
    Supports both:
      username@neverdue.ca
      username@user.neverdue.ca
    """
    from accounts.models import User

    try:
        local, domain = recipient.lower().split('@', 1)
        # subdomain format: username@user.neverdue.ca → local part is username directly
        # flat format: username@neverdue.ca → local part is username
        username = local.split('.')[0] if '.' in local else local
        return User.objects.get(username=username)
    except (User.DoesNotExist, IndexError, ValueError):
        return None


def sender_is_allowed(user, sender):
    """
    Check if a sender is allowed for a given user based on their FilterRules.
    - If user has no allow rules: all senders allowed unless blocked
    - If user has allow rules: only matching senders allowed (unless also blocked)
    Supports exact email, @domain.com, and glob patterns (fnmatch).
    """
    from dashboard.models import FilterRule
    from fnmatch import fnmatch

    rules = FilterRule.objects.filter(user=user)
    if not rules.exists():
        return True

    sender = sender.lower()
    allow_rules = [r.pattern.lower() for r in rules if r.action == 'allow']
    block_rules = [r.pattern.lower() for r in rules if r.action == 'block']

    def matches(pattern):
        if pattern.startswith('@'):
            return sender.endswith(pattern)
        return sender == pattern or fnmatch(sender, pattern)

    # Block takes priority
    if any(matches(p) for p in block_rules):
        return False

    # If allow list exists, sender must match
    if allow_rules:
        return any(matches(p) for p in allow_rules)

    return True