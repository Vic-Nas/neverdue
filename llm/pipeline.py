# llm/pipeline.py
import logging

from django.utils import timezone

from .extractor import extract_events, extract_events_from_image, extract_events_from_email
from .resolver import resolve_category
from dashboard.writer import write_event_to_calendar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NOTE — required change in extractor.py
# ---------------------------------------------------------------------------
# Each extract_* function must return a tuple (events, input_tokens, output_tokens)
# instead of just events. Example for extract_events:
#
#   def extract_events(text, language, user_timezone):
#       response = client.messages.create(...)
#       events = _parse_response(response)
#       return events, response.usage.input_tokens, response.usage.output_tokens
#
# Same pattern for extract_events_from_email and extract_events_from_image.
# ---------------------------------------------------------------------------


def _fire_usage(user, input_tokens: int, output_tokens: int) -> None:
    """
    Async-fire token usage tracking. Non-blocking — never raises.
    Skips silently if tokens are zero (e.g. early-exit paths).
    """
    if not input_tokens and not output_tokens:
        return
    try:
        from emails.tasks import track_llm_usage
        track_llm_usage.delay(user.pk, input_tokens, output_tokens)
    except Exception as exc:
        # Tracking must never break the pipeline.
        logger.warning("_fire_usage: failed to enqueue for user=%s: %s", user.pk, exc)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def process_text(user, text: str, sender: str = '', source_email_id: str = '', scan_job=None) -> tuple[list, str]:
    """
    Extract events from plain text or a reprocess prompt.

    Returns (created_events, notes).
    notes is non-empty only when a scan limit was hit.
    """
    if not _check_and_increment_scans(user):
        return [], 'Scan limit reached.'

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')

    try:
        events, input_tokens, output_tokens = extract_events(text, language=language, user_timezone=user_timezone)
    except ValueError as exc:
        logger.warning("process_text: extraction failed for user=%s: %s", user.pk, exc)
        return [], ''

    _fire_usage(user, input_tokens, output_tokens)
    logger.info("process_text: extracted %s event(s) for user=%s", len(events), user.pk)
    created = _save_events(user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job)
    return created, ''


def process_email(user, body: str, attachments: list, sender: str = '', source_email_id: str = '', scan_job=None) -> tuple[list, str]:
    """
    Extract events from an inbound email (body + optional attachments).
    Also used by process_uploaded_file (empty body, single attachment).

    attachments: list of [base64_string, media_type] or [base64_string, media_type, filename].
    Returns (created_events, notes).
    notes is non-empty when attachments were stripped (non-Pro) or scan limit hit.
    """
    import base64

    if not _check_and_increment_scans(user):
        return [], 'Scan limit reached.'

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')

    notes = ''
    decoded_attachments = []
    for entry in (attachments or []):
        try:
            b64_content, media_type = entry[0], entry[1]
            filename = entry[2] if len(entry) > 2 else ''
            decoded_attachments.append((base64.b64decode(b64_content), media_type, filename))
        except Exception:
            continue

    if decoded_attachments and not user.is_pro:
        decoded_attachments = []
        notes = 'Attachments ignored — Pro plan required.'

    try:
        events, input_tokens, output_tokens = extract_events_from_email(
            body=body or '',
            attachments=decoded_attachments,
            language=language,
            user_timezone=user_timezone,
        )
    except ValueError as exc:
        logger.warning("process_email: extraction failed for user=%s: %s", user.pk, exc)
        return [], notes

    _fire_usage(user, input_tokens, output_tokens)
    logger.info("process_email: extracted %s event(s) for user=%s", len(events), user.pk)
    created = _save_events(user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job)
    return created, notes


def process_file(user, file_bytes: bytes, media_type: str, context: str = '') -> tuple[list, str]:
    """
    Extract events from a raw file upload (image, PDF, or plain text).

    NOTE: The dashboard upload view routes through process_email so that filename
    context reaches the LLM consistently. This function is kept for any direct
    callers that have already decoded bytes.
    Returns (created_events, notes).
    """
    if not user.is_pro:
        return [], 'File uploads require a Pro plan.'

    if not _check_and_increment_scans(user):
        return [], 'Scan limit reached.'

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')

    if media_type == 'text/plain':
        text = file_bytes.decode('utf-8', errors='ignore')
        if context:
            text = f"{text}\n\nUser context: {context}"
        try:
            events, input_tokens, output_tokens = extract_events(text, language=language, user_timezone=user_timezone)
        except ValueError as exc:
            logger.warning("process_file: extraction failed for user=%s: %s", user.pk, exc)
            return [], ''
    else:
        try:
            events, input_tokens, output_tokens = extract_events_from_image(
                file_bytes, media_type, context=context, language=language, user_timezone=user_timezone,
            )
        except ValueError as exc:
            logger.warning("process_file: extraction failed for user=%s: %s", user.pk, exc)
            return [], ''

    _fire_usage(user, input_tokens, output_tokens)
    logger.info("process_file: extracted %s event(s) for user=%s", len(events), user.pk)
    created = _save_events(user, events)
    return created, ''


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_and_increment_scans(user) -> bool:
    """
    Enforce monthly scan limit for free users.
    Resets the counter if the calendar month has rolled over.
    Returns True if the scan is allowed.
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
    """Return (or lazily create) the Uncategorized category for a user."""
    from dashboard.models import Category
    category, _ = Category.objects.get_or_create(
        user=user,
        name='Uncategorized',
        defaults={'priority': 1},
    )
    return category


def _find_conflicts(user, event_data: dict) -> list:
    """
    Return existing active events that conflict with event_data.

    Conflict conditions (either triggers):
      1. Same source_email_id — this email was already successfully processed.
      2. Same title + start time within ±1 hour of an existing active event.

    Returns a list of conflicting Event instances (may be empty).
    """
    from dashboard.models import Event
    from datetime import timedelta
    from django.utils.dateparse import parse_datetime

    conflicts = []
    source_email_id = event_data.get('source_email_id', '')

    # Condition 1: same source email already has active events
    if source_email_id:
        by_email = list(
            Event.objects.filter(
                user=user,
                source_email_id=source_email_id,
                status='active',
            ).only('pk', 'title', 'start')
        )
        conflicts.extend(by_email)

    # Condition 2: same title + overlapping start time (±1 hour)
    title = event_data.get('title', '').strip()
    start_str = event_data.get('start', '')
    if title and start_str:
        try:
            start_dt = parse_datetime(start_str)
            if start_dt:
                window_start = start_dt - timedelta(hours=1)
                window_end = start_dt + timedelta(hours=1)
                by_title = list(
                    Event.objects.filter(
                        user=user,
                        title__iexact=title,
                        start__range=(window_start, window_end),
                        status='active',
                    ).exclude(
                        pk__in=[c.pk for c in conflicts]
                    ).only('pk', 'title', 'start')
                )
                conflicts.extend(by_title)
        except Exception:
            pass

    return conflicts


def _append_conflict_concern(event_data: dict, conflicts: list) -> dict:
    """
    Append conflict details to event_data['concern'] and ensure status is pending.
    Mutates and returns event_data.
    """
    lines = []
    for conflict in conflicts:
        date_str = conflict.start.strftime('%Y-%m-%d %H:%M') if conflict.start else '?'
        lines.append(f"Conflicts with existing event: '{conflict.title}' on {date_str} (id={conflict.pk}).")

    conflict_note = ' '.join(lines)
    existing_concern = event_data.get('concern', '').strip()
    event_data['concern'] = f"{existing_concern} {conflict_note}".strip() if existing_concern else conflict_note
    event_data['status'] = 'pending'
    return event_data


def _save_events(user, events: list, sender: str = '', source_email_id: str = '', scan_job=None) -> list:
    """
    Persist extracted events and set the final job status.

    Rules applied here (in order):
      1. Conflict detection — pending status + enriched concern for any event
         that clashes with an existing active event.
      2. All-or-nothing batch rule — if any event ends up pending, all events
         in the batch flip to pending (with a generic concern on the ones that
         were already active).
      3. Job status ownership — this function is the only place that sets
         needs_review or done on the job. The task layer never sets these.

    Returns the list of created Event instances.
    """
    from emails.models import ScanJob

    if not events:
        _finalise_job(scan_job, has_pending=False)
        return []

    # Stamp source_email_id on every event before any processing
    for event_data in events:
        event_data['source_email_id'] = source_email_id

    # Step 1: conflict detection — enrich pending concern where needed
    for event_data in events:
        conflicts = _find_conflicts(user, event_data)
        if conflicts:
            _append_conflict_concern(event_data, conflicts)

    # Step 2: all-or-nothing batch rule
    if any(e.get('status') == 'pending' for e in events):
        for e in events:
            if e.get('status') == 'active':
                e['status'] = 'pending'
                existing = e.get('concern', '').strip()
                batch_note = 'Other events in this batch needed attention.'
                e['concern'] = f"{existing} {batch_note}".strip() if existing else batch_note

    has_pending = any(e.get('status') == 'pending' for e in events)

    # Step 3: write events
    created = []
    for event_data in events:
        category = resolve_category(user, event_data, sender)
        if category is None:
            category = _get_or_create_uncategorized(user)
        event = write_event_to_calendar(user, event_data, category, scan_job=scan_job)
        if event:
            created.append(event)

    # Step 4: set terminal job status — pipeline owns this, not the task layer
    _finalise_job(scan_job, has_pending=has_pending)

    return created


def _finalise_job(scan_job, has_pending: bool) -> None:
    """
    Set the terminal status on a ScanJob.
    needs_review if any pending events were produced, done otherwise.
    The task layer must not set done/needs_review — only failed on exception.
    """
    if scan_job is None:
        return

    from emails.models import ScanJob

    if has_pending:
        new_status = ScanJob.STATUS_NEEDS_REVIEW
    else:
        new_status = ScanJob.STATUS_DONE

    ScanJob.objects.filter(pk=scan_job.pk).update(
        status=new_status,
        updated_at=timezone.now(),
    )
    logger.info(
        "_finalise_job: job=%s → %s (has_pending=%s)",
        scan_job.pk, new_status, has_pending,
    )