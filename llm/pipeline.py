# llm/pipeline.py
from django.utils import timezone
from .extractor import extract_events, extract_events_from_image
from .resolver import resolve_category
from dashboard.writer import write_event_to_calendar


def process_text(user, text: str, sender: str = '', source_email_id: str = '') -> list:
    """
    Full pipeline for plain text or email body.
    Returns list of created Event objects.
    """
    if not _check_and_increment_scans(user):
        return []

    language = getattr(user, 'language', 'English')

    try:
        events = extract_events(text, language=language)
    except ValueError:
        return []

    print(f"EXTRACTED EVENTS: {events}")
    return _save_events(user, events, sender, source_email_id)


def process_file(user, file_bytes: bytes, media_type: str, context: str = '') -> list:
    """
    Full pipeline for image, PDF, or text file upload.
    Returns list of created Event objects.
    """
    if not user.is_pro:
        return []

    if not _check_and_increment_scans(user):
        return []

    language = getattr(user, 'language', 'English')

    if media_type == 'text/plain':
        text = file_bytes.decode('utf-8', errors='ignore')
        if context:
            text = f"{text}\n\nUser context: {context}"
        try:
            events = extract_events(text, language=language)
        except ValueError:
            return []
    else:
        try:
            events = extract_events_from_image(file_bytes, media_type, context=context, language=language)
        except ValueError:
            return []

    print(f"EXTRACTED EVENTS: {events}")
    return _save_events(user, events)


def _check_and_increment_scans(user) -> bool:
    """
    Check scan limit for free users and reset monthly counter if needed.
    Returns True if scan is allowed.
    """
    today = timezone.now().date()

    if not user.scan_reset_date or user.scan_reset_date.month != today.month:
        user.monthly_scans = 0
        user.scan_reset_date = today
        user.save(update_fields=['monthly_scans', 'scan_reset_date'])

    if not user.is_pro and user.monthly_scans >= 30:
        return False

    user.monthly_scans += 1
    user.save(update_fields=['monthly_scans'])
    return True


def _save_events(user, events: list, sender: str = '', source_email_id: str = '') -> list:
    created = []
    for event_data in events:
        event_data['source_email_id'] = source_email_id
        category = resolve_category(user, event_data, sender)
        event = write_event_to_calendar(user, event_data, category)
        if event:
            created.append(event)
    return created
