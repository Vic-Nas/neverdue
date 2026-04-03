# llm/pipeline/entry.py
import base64
import logging

from ..extractor import extract_events, extract_events_from_email
from ..resolver import resolve_category, collect_prompt_injections, DISCARD
from .outcome import ProcessingOutcome
from .saving import _check_and_increment_scans, _fire_usage, _save_events, GCalUnavailableError

logger = logging.getLogger(__name__)


def process_text(user, text: str, sender: str = '', source_email_id: str = '', scan_job=None) -> ProcessingOutcome:
    if not _check_and_increment_scans(user):
        return ProcessingOutcome(
            status='failed', failure_reason='scan_limit',
            notes='Monthly scan limit reached. Will retry automatically on quota reset or plan upgrade.',
        )

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')
    user_instructions = collect_prompt_injections(user, sender)

    try:
        logger.debug("process_text: calling extract_events | user=%s text_len=%d", user.pk, len(text))
        events, input_tokens, output_tokens = extract_events(
            text, language=language, user_timezone=user_timezone,
            user_instructions=user_instructions,
        )
        logger.debug("process_text: extracted %d events | user=%s", len(events), user.pk)
    except ValueError as exc:
        logger.error("llm.process_text: extraction error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(status='failed', failure_reason='llm_error')

    _fire_usage(user, input_tokens, output_tokens)
    try:
        created, has_pending = _save_events(user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job)
    except GCalUnavailableError:
        return ProcessingOutcome(status='failed', failure_reason='gcal_disconnected',
            notes='Google Calendar is not connected. Reconnect or disable sync in Preferences.')
    return ProcessingOutcome(created=created, status='needs_review' if has_pending else 'done')


def process_email(user, body: str, attachments: list, sender: str = '', source_email_id: str = '', scan_job=None) -> ProcessingOutcome:
    if not _check_and_increment_scans(user):
        return ProcessingOutcome(
            status='failed', failure_reason='scan_limit',
            notes='Monthly scan limit reached. Will retry automatically on quota reset or plan upgrade.',
        )

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')

    decoded_attachments = []
    for entry in (attachments or []):
        try:
            b64_content, media_type = entry[0], entry[1]
            filename = entry[2] if len(entry) > 2 else ''
            decoded_attachments.append((base64.b64decode(b64_content), media_type, filename))
        except Exception:
            continue

    notes = ''
    if decoded_attachments and not user.is_pro:
        decoded_attachments = []
        notes = 'Upgrade to Pro to process files and attachments.'
        if not (body and body.strip()):
            return ProcessingOutcome(status='needs_review', notes=notes)

    user_instructions = collect_prompt_injections(user, sender)
    try:
        logger.debug("process_email: calling extract_events_from_email | user=%s body_len=%d attachments=%d", user.pk, len(body or ''), len(decoded_attachments))
        events, input_tokens, output_tokens = extract_events_from_email(
            body=body or '', attachments=decoded_attachments,
            language=language, user_timezone=user_timezone,
            user_instructions=user_instructions,
        )
        logger.debug("process_email: extracted %d events | user=%s", len(events), user.pk)
    except ValueError as exc:
        logger.error("llm.process_email: extraction error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(status='failed', failure_reason='llm_error', notes=notes)

    _fire_usage(user, input_tokens, output_tokens)
    try:
        created, has_pending = _save_events(user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job)
    except GCalUnavailableError:
        return ProcessingOutcome(status='failed', failure_reason='gcal_disconnected',
            notes='Google Calendar is not connected. Reconnect or disable sync in Preferences.')
    return ProcessingOutcome(created=created, notes=notes, status='needs_review' if has_pending else 'done')
