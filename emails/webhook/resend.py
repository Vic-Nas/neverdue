import logging

import requests
from django.conf import settings
from svix.webhooks import Webhook, WebhookVerificationError

logger = logging.getLogger(__name__)

SUPPORTED_ATTACHMENT_TYPES = {
    'application/pdf', 'image/jpeg', 'image/png',
    'image/webp', 'image/gif', 'text/plain',
}


def fetch_full_email(email_id: str) -> dict:
    api_key = getattr(settings, 'RESEND_API_KEY', '')
    if not api_key:
        logger.error("emails.fetch_full_email: RESEND_API_KEY not set")
        return {}
    try:
        response = requests.get(
            f"https://api.resend.com/emails/receiving/{email_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
        logger.error("emails.fetch_full_email: api error | email_id=%s status=%s", email_id, response.status_code)
        return {}
    except requests.RequestException as exc:
        logger.error("emails.fetch_full_email: request failed | email_id=%s error=%s", email_id, exc)
        return {}


def fetch_attachment_content(email_id: str, attachment_id: str) -> tuple[bytes, str] | None:
    api_key = getattr(settings, 'RESEND_API_KEY', '')
    try:
        response = requests.get(
            f"https://api.resend.com/emails/receiving/{email_id}/attachments/{attachment_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code != 200:
            logger.error("emails.fetch_attachment_content: api error | attachment_id=%s status=%s", attachment_id, response.status_code)
            return None

        data = response.json()
        download_url = data.get('download_url')
        if not download_url:
            logger.error("emails.fetch_attachment_content: no download_url | attachment_id=%s", attachment_id)
            return None

        dl_response = requests.get(download_url, timeout=30)
        if dl_response.status_code != 200:
            logger.error("emails.fetch_attachment_content: download failed | attachment_id=%s status=%s", attachment_id, dl_response.status_code)
            return None

        return dl_response.content, data.get('content_type', '')
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
