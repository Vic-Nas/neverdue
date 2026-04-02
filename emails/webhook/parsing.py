# emails/webhook/parsing.py
import base64
import logging

import html2text

from .resend import fetch_attachment_content, SUPPORTED_ATTACHMENT_TYPES

logger = logging.getLogger(__name__)

_h2t = html2text.HTML2Text()
_h2t.ignore_links = True
_h2t.ignore_images = True
_h2t.body_width = 0


def extract_email_text(full_email: dict) -> str:
    text = (full_email.get('text') or '').strip()
    if text:
        return text
    html = full_email.get('html') or ''
    if html:
        return _h2t.handle(html).strip()
    logger.error("emails.extract_email_text: no text or html | keys=%s", list(full_email.keys()))
    return ''


def extract_attachments(full_email: dict) -> list:
    email_id = full_email.get('id', '')
    attachment_metas = full_email.get('attachments', [])
    if not email_id or not attachment_metas:
        return []

    result = []
    for idx, attachment in enumerate(attachment_metas):
        content_type = (attachment.get('content_type') or '').split(';')[0].strip().lower()
        attachment_id = attachment.get('id')
        filename = attachment.get('filename') or attachment.get('name') or ''

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
        result.append([b64, final_type, filename])

    return result
