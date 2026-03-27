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
    user_timezone = getattr(user, 'timezone', 'UTC')

    try:
        events = extract_events(text, language=language, user_timezone=user_timezone)
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
    user_timezone = getattr(user, 'timezone', 'UTC')

    if media_type == 'text/plain':
        text = file_bytes.decode('utf-8', errors='ignore')
        if context:
            text = f"{text}\n\nUser context: {context}"
        try:
            events = extract_events(text, language=language, user_timezone=user_timezone)
        except ValueError:
            return []
    else:
        try:
            events = extract_events_from_image(file_bytes, media_type, context=context, language=language, user_timezone=user_timezone)
        except ValueError:
            return []

    print(f"EXTRACTED EVENTS: {events}")
    return _save_events(user, events)


def process_email(user, body: str, attachments: list, sender: str = '', source_email_id: str = '') -> list:
    """
    Full pipeline for an inbound email with optional attachments.
    Body and attachments are sent together in a single LLM call.
    attachments: list of (base64_string, media_type) tuples (as received from webhook).
    """
    from .extractor import extract_events_from_email
    import base64

    if not _check_and_increment_scans(user):
        return []

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')

    # Decode b64 attachments to bytes
    decoded_attachments = []
    for b64_content, media_type in (attachments or []):
        try:
            decoded_attachments.append((base64.b64decode(b64_content), media_type))
        except Exception:
            continue

    try:
        events = extract_events_from_email(
            body=body or '',
            attachments=decoded_attachments,
            language=language,
            user_timezone=user_timezone,
        )
    except ValueError:
        return []

    print(f"EXTRACTED EVENTS: {events}")
    return _save_events(user, events, sender, source_email_id)


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


def _get_or_create_uncategorized(user):
    """
    Lazily create the Uncategorized category for a user.
    Always priority=1 (low). Created only when needed.
    """
    from dashboard.models import Category
    category, _ = Category.objects.get_or_create(
        user=user,
        name='Uncategorized',
        defaults={'priority': 1},
    )
    return category


def _save_events(user, events: list, sender: str = '', source_email_id: str = '') -> list:
    created = []
    for event_data in events:
        event_data['source_email_id'] = source_email_id
        category = resolve_category(user, event_data, sender)
        if category is None:
            category = _get_or_create_uncategorized(user)
        event = write_event_to_calendar(user, event_data, category)
        if event:
            created.append(event)
    return created
