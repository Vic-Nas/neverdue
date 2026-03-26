# emails/webhook.py
import base64
import hashlib
import hmac
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def fetch_full_email(email_id: str) -> dict:
    """
    Fetch full email content from Resend's Received Emails API.
    Resend webhooks only contain metadata — body and attachments
    must be retrieved separately via this API call.
    https://resend.com/docs/api-reference/emails/retrieve-email
    """
    api_key = getattr(settings, 'RESEND_API_KEY', '')
    if not api_key:
        logger.error("RESEND_API_KEY is not set — cannot fetch email body")
        return {}

    url = f"https://api.resend.com/emails/{email_id}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            if settings.DEBUG:
                logger.debug(
                    "[DEBUG] fetch_full_email | email_id=%s | keys=%s",
                    email_id, list(data.keys()),
                )
            return data
        else:
            logger.error(
                "fetch_full_email failed | email_id=%s | status=%s | response=%s",
                email_id, response.status_code, response.text,
            )
            return {}
    except requests.RequestException as exc:
        logger.error("fetch_full_email request error | email_id=%s | %s", email_id, exc)
        return {}

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


def extract_email_text(payload, full_email: dict = None):
    """
    Extract plain text body from Resend inbound webhook payload.
    
    Because Resend webhooks only include metadata, pass `full_email`
    (the result of fetch_full_email) to get the actual body.
    Falls back to stripping HTML if plain text is absent.
    """
    # Prefer full_email from the Resend API (has 'text' and 'html')
    source = full_email if full_email else (payload.get('data') or payload)

    if settings.DEBUG:
        logger.debug(
            "[DEBUG] extract_email_text | source=full_email=%s | keys=%s",
            bool(full_email), list(source.keys()),
        )

    text = (source.get('text') or '').strip()
    if text:
        if settings.DEBUG:
            logger.debug("[DEBUG] Found plain text body, len=%s | preview=%r", len(text), text[:200])
        return text

    if settings.DEBUG:
        logger.debug("[DEBUG] No plain text found — trying HTML fallback")

    html = source.get('html') or ''
    if html:
        import re
        stripped = re.sub(r'<[^>]+>', ' ', html)
        stripped = re.sub(r'\s+', ' ', stripped).strip()
        if settings.DEBUG:
            logger.debug("[DEBUG] Fell back to HTML stripping | stripped_len=%s | preview=%r", len(stripped), stripped[:200])
        return stripped

    logger.warning(
        "extract_email_text: no text or HTML found. "
        "Top-level keys=%s | full_email provided=%s",
        list(source.keys()), bool(full_email),
    )
    return ''


def extract_attachments(payload, full_email: dict = None):
    """
    Extract supported attachments from Resend inbound webhook payload.
    Pass `full_email` (result of fetch_full_email) to get actual attachment content.
    Returns list of (base64_string, media_type) tuples — NOT decoded bytes.
    tasks.py is responsible for base64 decoding.
    """
    source = full_email if full_email else (payload.get('data') or payload)
    attachments = source.get('attachments', [])
    result = []

    if settings.DEBUG:
        logger.debug("[DEBUG] extract_attachments | found %s attachment(s)", len(attachments))

    for idx, attachment in enumerate(attachments):
        content_type = attachment.get('contentType') or attachment.get('content-type') or attachment.get('type', '')
        content_type = content_type.split(';')[0].strip().lower()

        if content_type not in SUPPORTED_ATTACHMENT_TYPES:
            if settings.DEBUG:
                logger.debug("[DEBUG] Attachment %s skipped — unsupported type: %s", idx, content_type)
            continue

        # Generic/Postmark: base64 string in 'content'
        content = attachment.get('content', '')
        if not content:
            logger.warning("Attachment %s has no content (content_type=%s)", idx, content_type)
            continue

        # Validate it's actually decodable before passing downstream
        try:
            base64.b64decode(content)
        except Exception as exc:
            logger.warning("Failed to validate attachment %s base64: %s", idx, exc)
            continue

        if settings.DEBUG:
            logger.debug("[DEBUG] Attachment %s accepted | type=%s", idx, content_type)

        # Return raw b64 string — tasks.py decodes it
        result.append((content, content_type))

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