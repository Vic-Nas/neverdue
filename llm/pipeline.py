# llm/pipeline.py
import logging
from dataclasses import dataclass, field

from django.utils import timezone

from .extractor import extract_events, extract_events_from_image, extract_events_from_email
from .resolver import resolve_category, collect_prompt_injections, DISCARD
from dashboard.writer import write_event_to_calendar

logger = logging.getLogger(__name__)

@dataclass
class ProcessingOutcome:
    """
    Returned by every pipeline entry point.

    Pipeline functions never write to the database. The task layer (tasks.py)
    reads this dataclass and writes all job state via _apply_outcome.
    This means you can read tasks.py to trace every job state transition
    without opening pipeline code.
    """
    created:        list = field(default_factory=list)
    notes:          str  = ''
    status:         str  = 'done'   # 'done' | 'needs_review' | 'failed'
    failure_reason: str  = ''       # ScanJob.REASON_* constant or ''


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def process_text(user, text: str, sender: str = '', source_email_id: str = '', scan_job=None) -> ProcessingOutcome:
    """
    Extract events from plain text or a reprocess prompt.

    Returns ProcessingOutcome. On scan limit the outcome status is 'failed'.
    No DB writes — the task layer applies the outcome.
    """
    if not _check_and_increment_scans(user):
        return ProcessingOutcome(
            status='failed',
            failure_reason='scan_limit',
            notes='Monthly scan limit reached. Will retry automatically on quota reset or plan upgrade.',
        )

    language = getattr(user, 'language', 'English')
    user_timezone = getattr(user, 'timezone', 'UTC')
    user_instructions = collect_prompt_injections(user, sender)

    try:
        events, input_tokens, output_tokens = extract_events(
            text, language=language, user_timezone=user_timezone,
            user_instructions=user_instructions,
        )
    except ValueError as exc:
        logger.error("llm.process_text: extraction error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(
            status='failed',
            failure_reason='llm_error',
        )

    _fire_usage(user, input_tokens, output_tokens)
    created, has_pending = _save_events(user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job)
    return ProcessingOutcome(
        created=created,
        status='needs_review' if has_pending else 'done',
    )


def process_email(user, body: str, attachments: list, sender: str = '', source_email_id: str = '', scan_job=None) -> ProcessingOutcome:
    """
    Extract events from an inbound email (body + optional attachments).
    Also used by process_uploaded_file (empty body, single attachment).

    attachments: list of [base64_string, media_type] or [base64_string, media_type, filename].

    Free users with an attachment-only email (no usable body) receive a failed
    outcome with reason=pro_required — the job stays visible and is retried
    automatically after a plan upgrade.

    Returns ProcessingOutcome. No DB writes.
    """
    import base64

    if not _check_and_increment_scans(user):
        return ProcessingOutcome(
            status='failed',
            failure_reason='scan_limit',
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
            return ProcessingOutcome(
                status='needs_review',
                notes=notes,
            )

    user_instructions = collect_prompt_injections(user, sender)
    try:
        events, input_tokens, output_tokens = extract_events_from_email(
            body=body or '',
            attachments=decoded_attachments,
            language=language,
            user_timezone=user_timezone,
            user_instructions=user_instructions,
        )
    except ValueError as exc:
        logger.error("llm.process_email: extraction error | user=%s error=%s", user.pk, exc)
        return ProcessingOutcome(
            status='failed',
            failure_reason='llm_error',
            notes=notes,
        )

    _fire_usage(user, input_tokens, output_tokens)
    created, has_pending = _save_events(user, events, sender=sender, source_email_id=source_email_id, scan_job=scan_job)
    return ProcessingOutcome(
        created=created,
        notes=notes,
        status='needs_review' if has_pending else 'done',
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_and_increment_scans(user) -> bool:
    """
    Enforce monthly scan limit for free users. Atomic to prevent races.

    Uses a conditional UPDATE so two concurrent workers cannot both pass
    the limit check and both increment — only one UPDATE succeeds per slot.
    Returns True if the scan is allowed and the counter was incremented.
    """
    from django.db.models import F
    from accounts.models import User

    today = timezone.now().date()

    # Reset counter if the calendar month has rolled over.
    if not user.scan_reset_date or user.scan_reset_date.month != today.month:
        User.objects.filter(pk=user.pk).update(monthly_scans=0, scan_reset_date=today)
        user.refresh_from_db(fields=['monthly_scans', 'scan_reset_date'])

    if user.is_pro:
        User.objects.filter(pk=user.pk).update(monthly_scans=F('monthly_scans') + 1)
        return True

    # Atomic conditional increment: only updates (and returns 1) if still under limit.
    updated = User.objects.filter(
        pk=user.pk,
        monthly_scans__lt=30,
    ).update(monthly_scans=F('monthly_scans') + 1)
    return bool(updated)


def _fire_usage(user, input_tokens: int, output_tokens: int) -> None:
    """Async-fire token usage tracking. Non-blocking — never raises."""
    if not input_tokens and not output_tokens:
        return
    try:
        from emails.tasks import track_llm_usage
        track_llm_usage.defer(user_id=user.pk, input_tokens=input_tokens, output_tokens=output_tokens)
    except Exception as exc:
        logger.error("llm._fire_usage: enqueue failed | user=%s error=%s", user.pk, exc)


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
    """
    from dashboard.models import Event
    from datetime import timedelta
    from django.utils.dateparse import parse_datetime

    conflicts = []
    source_email_id = event_data.get('source_email_id', '')

    if source_email_id:
        by_email = list(
            Event.objects.filter(
                user=user, source_email_id=source_email_id, status='active',
            ).only('pk', 'title', 'start')
        )
        conflicts.extend(by_email)

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
                    ).exclude(pk__in=[c.pk for c in conflicts])
                    .only('pk', 'title', 'start')
                )
                conflicts.extend(by_title)
        except Exception:
            pass

    return conflicts


def _append_conflict_concern(event_data: dict, conflicts: list) -> dict:
    """Append conflict details to event_data['concern'] and force status to pending."""
    lines = [
        f"Conflicts with existing event: '{c.title}' on "
        f"{c.start.strftime('%Y-%m-%d %H:%M') if c.start else '?'} (id={c.pk})."
        for c in conflicts
    ]
    conflict_note = ' '.join(lines)
    existing = event_data.get('concern', '').strip()
    event_data['concern'] = f"{existing} {conflict_note}".strip() if existing else conflict_note
    event_data['status'] = 'pending'
    return event_data


def _save_events(user, events: list, sender: str = '', source_email_id: str = '', scan_job=None) -> tuple[list, bool]:
    """
    Persist extracted events. Returns (created_list, has_pending).

    Rules applied in order:
      1. Stamp source_email_id on every event.
      2. Conflict detection — pending status + enriched concern for any event
         that clashes with an existing active event.
      3. All-or-nothing batch rule — if any event is pending, all flip to pending.
      4. Write events via write_event_to_calendar.

    Does not touch the database for job state — that is the task layer's job.
    """
    if not events:
        return [], False

    for event_data in events:
        event_data['source_email_id'] = source_email_id

    for event_data in events:
        conflicts = _find_conflicts(user, event_data)
        if conflicts:
            _append_conflict_concern(event_data, conflicts)

    if any(e.get('status') == 'pending' for e in events):
        for e in events:
            if e.get('status') == 'active':
                e['status'] = 'pending'
                existing = e.get('concern', '').strip()
                batch_note = 'Other events in this batch needed attention.'
                e['concern'] = f"{existing} {batch_note}".strip() if existing else batch_note

    has_pending = any(e.get('status') == 'pending' for e in events)

    created = []
    for event_data in events:
        category = resolve_category(user, event_data, sender)
        if category is DISCARD:
            continue
        if category is None:
            category = _get_or_create_uncategorized(user)
        event = write_event_to_calendar(user, event_data, category, scan_job=scan_job)
        if event:
            created.append(event)

    return created, has_pending