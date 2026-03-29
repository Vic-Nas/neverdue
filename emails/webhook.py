# emails/webhook.py
import logging

import requests
from django.conf import settings
from svix.webhooks import Webhook, WebhookVerificationError
import html2text

logger = logging.getLogger(__name__)

_h2t = html2text.HTML2Text()
_h2t.ignore_links = True
_h2t.ignore_images = True
_h2t.body_width = 0  # no line wrapping


def fetch_full_email(email_id: str) -> dict:
    """
    Fetch full email content from Resend's Received Emails API.
    Resend webhooks only contain metadata — body and attachments
    must be retrieved separately via this API call.
    https://resend.com/docs/api-reference/emails/retrieve-email
    """
    api_key = getattr(settings, 'RESEND_API_KEY', '')
    if not api_key:
        logger.error("emails.fetch_full_email: RESEND_API_KEY not set")
        return {}

    url = f"https://api.resend.com/emails/receiving/{email_id}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data
        else:
            logger.error(
                "emails.fetch_full_email: api error | email_id=%s status=%s",
                email_id, response.status_code,
            )
            return {}
    except requests.RequestException as exc:
        logger.error("emails.fetch_full_email: request failed | email_id=%s error=%s", email_id, exc)
        return {}

SUPPORTED_ATTACHMENT_TYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
    'text/plain',
}


def fetch_attachment_content(email_id: str, attachment_id: str) -> tuple[bytes, str] | None:
    """
    Fetch a single attachment's content via the Resend Attachments API.
    Returns (raw_bytes, content_type) or None on failure.
    """
    api_key = getattr(settings, 'RESEND_API_KEY', '')
    url = f"https://api.resend.com/emails/receiving/{email_id}/attachments/{attachment_id}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code != 200:
            logger.error("emails.fetch_attachment_content: api error | attachment_id=%s status=%s", attachment_id, response.status_code)
            return None

        data = response.json()
        download_url = data.get('download_url')
        content_type = data.get('content_type', '')

        if not download_url:
            logger.error("emails.fetch_attachment_content: no download_url | attachment_id=%s", attachment_id)
            return None

        dl_response = requests.get(download_url, timeout=30)
        if dl_response.status_code != 200:
            logger.error("emails.fetch_attachment_content: download failed | attachment_id=%s status=%s", attachment_id, dl_response.status_code)
            return None

        raw_bytes = dl_response.content
        return raw_bytes, content_type

    except requests.RequestException as exc:
        logger.error("emails.fetch_attachment_content: request failed | attachment_id=%s error=%s", attachment_id, exc)
        return None


def verify_resend_signature(payload_bytes: bytes, headers: dict) -> bool:
    secret = getattr(settings, "RESEND_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("emails.verify_resend_signature: RESEND_WEBHOOK_SECRET not set")
        return False
    wh = Webhook(secret)
    svix_headers = {
        "svix-id":        headers.get("HTTP_SVIX_ID", ""),
        "svix-timestamp": headers.get("HTTP_SVIX_TIMESTAMP", ""),
        "svix-signature": headers.get("HTTP_SVIX_SIGNATURE", ""),
    }
    try:
        wh.verify(payload_bytes, svix_headers)
        return True
    except WebhookVerificationError as exc:
        logger.error("emails.verify_resend_signature: verification failed | error=%s", exc)
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

    text = (source.get('text') or '').strip()
    if text:
        return text

    html = source.get('html') or ''
    if html:
        stripped = _h2t.handle(html).strip()
        return stripped

    logger.error(
        "emails.extract_email_text: no text or html | keys=%s",
        list(source.keys()),
    )
    return ''


def extract_attachments(payload, full_email: dict = None):
    """
    Extract supported attachments using Resend's Attachments API.
    Attachment metadata comes from the webhook payload; content is fetched separately.
    Returns list of (base64_string, media_type) tuples.
    """
    data = payload.get('data', {})
    email_id = data.get('email_id')
    attachment_metas = data.get('attachments', [])  # metadata only, from webhook

    if not email_id or not attachment_metas:
        return []

    result = []
    for idx, attachment in enumerate(attachment_metas):
        content_type = (attachment.get('content_type') or '').split(';')[0].strip().lower()
        attachment_id = attachment.get('id')

        if content_type not in SUPPORTED_ATTACHMENT_TYPES:
            continue

        if not attachment_id:
            logger.error("emails.extract_attachments: attachment missing id | index=%s", idx)
            continue

        fetched = fetch_attachment_content(email_id, attachment_id)
        if fetched is None:
            continue

        raw_bytes, fetched_content_type = fetched
        final_type = fetched_content_type or content_type
        b64 = base64.b64encode(raw_bytes).decode()

        result.append((b64, final_type))

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
      username@user.neverdue.ca
      username@user.neverdue.ca
    """
    from accounts.models import User

    try:
        local, domain = recipient.lower().split('@', 1)
        username = local.split('.')[0] if '.' in local else local
        user = User.objects.get(username=username)
        return user
    except User.DoesNotExist:
        logger.error("emails.get_user_from_recipient: user not found | recipient=%s", recipient)
        return None
    except (IndexError, ValueError) as exc:
        logger.error("emails.get_user_from_recipient: parse failed | recipient=%s error=%s", recipient, exc)
        return None


def sender_is_allowed(user, sender):
    """
    Check if a sender is allowed for a given user based on their FilterRules.
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

    if any(matches(p) for p in block_rules):
        return False

    if allow_rules:
        allowed = any(matches(p) for p in allow_rules)
        return allowed

    return True