# llm/pipeline/entry.py
import base64
import logging

from ..extractor import extract_events, extract_events_from_email, LLMAPIError
from ..resolver import collect_prompt_injections
from .outcome import ProcessingOutcome
from .saving import _check_and_increment_scans, _fire_usage, _save_events, GCalUnavailableError

logger = logging.getLogger(__name__)


def _fetch_category_names(user) -> list[str]:
    """Return the user's existing category names for LLM hint grounding."""
    from dashboard.models import Category
    return list(Category.objects.filter(user=user).values_list('name', flat=True))


def process_text(user, text: str, sender: str = '', source_email_id: str = '', scan_job=None) -> ProcessingOutcome:
    if not _check_and_increment_scans(user):
        return ProcessingOutcome(
            status='failed', failure_reason='scan_limit',
            notes='Monthly scan limit reached. Will retry automatically on quota reset or plan upgrade.',
        )

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')
    user_instructions = collect_prompt_injections(user, sender)
    existing_categories = _fetch_category_names(user)

    try:
        logger.debug("process_text: calling extract_events | user=%s text_len=%d", user.pk, len(text))
        events, input_tokens, output_tokens = extract_events(
            text, language=language, user_timezone=user_timezone,
            user_instructions=user_instructions,
            existing_categories=existing_categories,
        )
        logger.debug("process_text: extracted %d events | user=%s", len(events), user.pk)
    except LLMAPIError as exc:
        logger.error("llm.process_text: API error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(status='failed', failure_reason='llm_error', notes=str(exc))
    except ValueError as exc:
        logger.error("llm.process_text: extraction error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(status='failed', failure_reason='llm_error')

    _fire_usage(user, input_tokens, output_tokens)
    try:
        created, has_pending, discarded_events = _save_events(
            user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job,
        )
    except GCalUnavailableError:
        return ProcessingOutcome(status='failed', failure_reason='gcal_disconnected',
            notes='Google Calendar is not connected. Reconnect or disable sync in Preferences.')

    discarded = len(discarded_events)
    discard_note = f'{discarded} event{"s" if discarded != 1 else ""} discarded by rule.' if discarded else ''

    if not created and discarded:
        return ProcessingOutcome(
            status='done',
            notes=discard_note,
            discarded_events=discarded_events,
        )
    return ProcessingOutcome(
        created=created,
        notes=discard_note,
        status='needs_review' if has_pending else 'done',
        discarded_events=discarded_events,
    )


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
    existing_categories = _fetch_category_names(user)

    try:
        logger.debug("process_email: calling extract_events_from_email | user=%s body_len=%d attachments=%d", user.pk, len(body or ''), len(decoded_attachments))
        events, input_tokens, output_tokens = extract_events_from_email(
            body=body or '', attachments=decoded_attachments,
            language=language, user_timezone=user_timezone,
            user_instructions=user_instructions,
            existing_categories=existing_categories,
        )
        logger.debug("process_email: extracted %d events | user=%s", len(events), user.pk)
    except LLMAPIError as exc:
        logger.error("llm.process_email: API error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(status='failed', failure_reason='llm_error', notes=str(exc))
    except ValueError as exc:
        logger.error("llm.process_email: extraction error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(status='failed', failure_reason='llm_error', notes=notes)

    _fire_usage(user, input_tokens, output_tokens)
    try:
        created, has_pending, discarded_events = _save_events(
            user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job,
        )
    except GCalUnavailableError:
        return ProcessingOutcome(status='failed', failure_reason='gcal_disconnected',
            notes='Google Calendar is not connected. Reconnect or disable sync in Preferences.')

    discarded = len(discarded_events)
    discard_note = f'{discarded} event{"s" if discarded != 1 else ""} discarded by rule.' if discarded else ''

    if not created and discarded:
        combined_notes = f'{discard_note} {notes}'.strip()
        return ProcessingOutcome(
            status='done',
            notes=combined_notes,
            discarded_events=discarded_events,
        )
    return ProcessingOutcome(
        created=created,
        notes=f'{discard_note} {notes}'.strip() if discard_note else notes,
        status='needs_review' if has_pending else 'done',
        discarded_events=discarded_events,
    )