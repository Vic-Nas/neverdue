import base64
import json
import logging

from django.conf import settings

from .client import call_api
from .prompts import SYSTEM_PROMPT, RECONCILIATION_PROMPT
from .utils import get_tz, today_in_tz, is_informative_filename
from .validation import parse_and_validate

logger = logging.getLogger(__name__)


def extract_events_from_email(
    body: str, attachments: list[tuple[bytes, str, str]],
    language: str = 'English', user_timezone: str = 'UTC', user_instructions: str = '',
) -> tuple[list[dict], int, int]:
    tz = get_tz(user_timezone)
    today = today_in_tz(tz)
    system = SYSTEM_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'

    attachment_events, non_visual, total_in, total_out = _extract_from_attachments(
        attachments, system, body, user_instructions, user_timezone, today, tz,
    )

    if not body and not non_visual:
        return attachment_events, total_in, total_out

    return _reconcile(
        attachment_events, non_visual, body, user_instructions,
        language, user_timezone, today, tz, system, total_in, total_out,
    )


def _extract_from_attachments(attachments, system, body, user_instructions, user_timezone, today, tz):
    attachment_events = []
    non_visual = []
    total_in = total_out = 0

    for file_bytes, media_type, filename in attachments:
        if media_type in ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'):
            content = []
            if is_informative_filename(filename):
                content.append({'type': 'text', 'text': f'Attachment filename: {filename}'})
            encoded = base64.standard_b64encode(file_bytes).decode('utf-8')
            if media_type == 'application/pdf':
                content.append({'type': 'document', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}})
            else:
                content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}})

            step1_text = f"Today's date: {today}\nUser's timezone: {user_timezone}\n\nExtract all calendar events from this file."
            if body:
                step1_text += f'\n\nUser context (follow strictly): {body}'
            if user_instructions:
                step1_text += f'\n\nUser instructions (follow strictly): {user_instructions}'
            content.append({'type': 'text', 'text': step1_text})

            try:
                message = call_api(model=settings.LLM_MODEL, max_tokens=2000, system=system, messages=[{'role': 'user', 'content': content}])
                attachment_events.extend(parse_and_validate(message, tz))
                total_in += message.usage.input_tokens
                total_out += message.usage.output_tokens
            except ValueError as exc:
                logger.error("llm.extract_events_from_email: step1 error | media_type=%s error=%s", media_type, exc)
        else:
            non_visual.append((file_bytes, media_type, filename))

    return attachment_events, non_visual, total_in, total_out


def _reconcile(attachment_events, non_visual, body, user_instructions, language, user_timezone, today, tz, system, total_in, total_out):
    recon_content = []

    for file_bytes, media_type, filename in non_visual:
        if is_informative_filename(filename):
            recon_content.append({'type': 'text', 'text': f'Attachment filename: {filename}'})
        encoded = base64.standard_b64encode(file_bytes).decode('utf-8')
        if media_type == 'application/pdf':
            recon_content.append({'type': 'document', 'source': {'type': 'base64', 'media_type': media_type, 'data': encoded}})
        else:
            recon_content.append({'type': 'text', 'text': file_bytes.decode('utf-8', errors='ignore')})

    recon_text = f"Today's date: {today}\nUser's timezone: {user_timezone}\n\n"
    if user_instructions:
        recon_text += f"\nUser instructions (follow strictly):\n{user_instructions}\n"
    if attachment_events:
        recon_text += f"Events already extracted from attachments (dates and times are ground truth):\n{json.dumps(attachment_events, ensure_ascii=False)}\n\n"
    if body:
        recon_text += f"{'Email body' if attachment_events else 'Extract all calendar events from this content'}:\n\n{body}"
    recon_content.append({'type': 'text', 'text': recon_text})

    if attachment_events:
        recon_system = RECONCILIATION_PROMPT + f'\n\nRespond in {language}. Event titles, descriptions, category hints, and concern messages must be in {language}.'
    else:
        recon_system = system

    try:
        message = call_api(model=settings.LLM_MODEL, max_tokens=2000, system=recon_system, messages=[{'role': 'user', 'content': recon_content}])
        events = parse_and_validate(message, tz)
        return events, total_in + message.usage.input_tokens, total_out + message.usage.output_tokens
    except ValueError as exc:
        logger.error("llm.extract_events_from_email: step2 error | fallback_events=%d error=%s", len(attachment_events), exc)
        return attachment_events, total_in, total_out
