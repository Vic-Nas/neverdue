# emails/webhook.py
import base64
import hashlib
import hmac
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)

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
        logger.warning("RESEND_WEBHOOK_SECRET is not set — rejecting webhook")
        return False

    msg_id = headers.get('HTTP_SVIX_ID', '')
    msg_timestamp = headers.get('HTTP_SVIX_TIMESTAMP', '')
    msg_signature = headers.get('HTTP_SVIX_SIGNATURE', '')

    if not all([msg_id, msg_timestamp, msg_signature]):
        logger.warning("Missing Svix headers: id=%r timestamp=%r signature=%r", msg_id, msg_timestamp, msg_signature)
        return False

    try:
        if abs(int(time.time()) - int(msg_timestamp)) > 300:
            logger.warning("Svix timestamp too old: %s", msg_timestamp)
            return False
    except ValueError:
        logger.warning("Invalid Svix timestamp: %r", msg_timestamp)
        return False

    try:
        secret_bytes = base64.b64decode(secret.replace('whsec_', ''))
    except Exception as exc:
        logger.warning("Failed to decode RESEND_WEBHOOK_SECRET: %s", exc)
        return False

    signed_content = f'{msg_id}.{msg_timestamp}.{payload_bytes.decode()}'

    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode(), hashlib.sha256).digest()
    ).decode()

    for sig in msg_signature.split(' '):
        if sig.startswith('v1,') and hmac.compare_digest(sig[3:], expected):
            return True

    logger.warning("Svix signature verification failed for msg_id=%s", msg_id)
    return False


def extract_email_text(payload):
    """
    Extract plain text body from Resend inbound webhook payload.
    Falls back to stripping HTML if plain text is absent.
    """
    # Resend sometimes puts content at root, sometimes under 'data'
    data = payload.get('data') or payload

    if settings.DEBUG:
        logger.debug("[DEBUG] extract_email_text | top-level keys=%s | data keys=%s", list(payload.keys()), list(data.keys()))

    text = data.get('text', '').strip()
    if text:
        if settings.DEBUG:
            logger.debug("[DEBUG] Found plain text body, len=%s | preview=%r", len(text), text[:200])
        return text

    if settings.DEBUG:
        logger.debug("[DEBUG] No plain text found under data['text']")

    html = data.get('html', '')
    if html:
        import re
        stripped = re.sub(r'<[^>]+>', ' ', html)
        stripped = re.sub(r'\s+', ' ', stripped).strip()
        if settings.DEBUG:
            logger.debug("[DEBUG] Fell back to HTML stripping | html_len=%s | stripped_len=%s | preview=%r", len(html), len(stripped), stripped[:200])
        return stripped

    logger.warning(
        "extract_email_text: no text or HTML found in payload. "
        "Top-level keys=%s | data keys=%s",
        list(payload.keys()),
        list(data.keys()),
    )
    return ''


def extract_attachments(payload):
    """
    Extract supported attachments from Resend inbound webhook payload.
    Returns list of (bytes, media_type) tuples.
    """
    data = payload.get('data', {})
    attachments = data.get('attachments', [])
    result = []

    if settings.DEBUG:
        logger.debug("[DEBUG] extract_attachments | found %s attachment(s)", len(attachments))

    for idx, attachment in enumerate(attachments):
        content_type = attachment.get('contentType', '').split(';')[0].strip().lower()
        if content_type not in SUPPORTED_ATTACHMENT_TYPES:
            if settings.DEBUG:
                logger.debug("[DEBUG] Attachment %s skipped — unsupported type: %s", idx, content_type)
            continue
        content = attachment.get('content', '')
        if not content:
            logger.warning("Attachment %s has no content (content_type=%s)", idx, content_type)
            continue
        try:
            file_bytes = base64.b64decode(content)
            if settings.DEBUG:
                logger.debug("[DEBUG] Attachment %s accepted | type=%s | size=%s bytes", idx, content_type, len(file_bytes))
            result.append((file_bytes, content_type))
        except Exception as exc:
            logger.warning("Failed to decode attachment %s: %s", idx, exc)
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

    if settings.DEBUG:
        logger.debug("[DEBUG] get_user_from_recipient | recipient=%s", recipient)

    try:
        local, domain = recipient.lower().split('@', 1)
        username = local.split('.')[0] if '.' in local else local
        user = User.objects.get(username=username)
        if settings.DEBUG:
            logger.debug("[DEBUG] Resolved recipient=%s → user=%s (pk=%s)", recipient, username, user.pk)
        return user
    except User.DoesNotExist:
        logger.warning("get_user_from_recipient: no user found for recipient=%s", recipient)
        return None
    except (IndexError, ValueError) as exc:
        logger.warning("get_user_from_recipient: failed to parse recipient=%s: %s", recipient, exc)
        return None


def sender_is_allowed(user, sender):
    """
    Check if a sender is allowed for a given user based on their FilterRules.
    """
    from dashboard.models import FilterRule
    from fnmatch import fnmatch

    rules = FilterRule.objects.filter(user=user)
    if not rules.exists():
        if settings.DEBUG:
            logger.debug("[DEBUG] sender_is_allowed: no rules for user=%s — allowing sender=%s", user.pk, sender)
        return True

    sender = sender.lower()
    allow_rules = [r.pattern.lower() for r in rules if r.action == 'allow']
    block_rules = [r.pattern.lower() for r in rules if r.action == 'block']

    if settings.DEBUG:
        logger.debug(
            "[DEBUG] sender_is_allowed | user=%s | sender=%s | allow_rules=%s | block_rules=%s",
            user.pk, sender, allow_rules, block_rules,
        )

    def matches(pattern):
        if pattern.startswith('@'):
            return sender.endswith(pattern)
        return sender == pattern or fnmatch(sender, pattern)

    if any(matches(p) for p in block_rules):
        logger.info("sender_is_allowed: sender=%s BLOCKED for user=%s", sender, user.pk)
        return False

    if allow_rules:
        allowed = any(matches(p) for p in allow_rules)
        if not allowed:
            logger.info("sender_is_allowed: sender=%s not in allow list for user=%s", sender, user.pk)
        return allowed

    return True