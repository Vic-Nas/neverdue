import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from dashboard.writer import write_event_to_calendar

logger = logging.getLogger(__name__)


def _check_and_increment_scans(user) -> bool:
    from django.db.models import F
    from accounts.models import User

    today = timezone.now().date()

    if not user.scan_reset_date or user.scan_reset_date.month != today.month:
        User.objects.filter(pk=user.pk).update(monthly_scans=0, scan_reset_date=today)
        user.refresh_from_db(fields=['monthly_scans', 'scan_reset_date'])

    if user.is_pro:
        User.objects.filter(pk=user.pk).update(monthly_scans=F('monthly_scans') + 1)
        return True

    updated = User.objects.filter(pk=user.pk, monthly_scans__lt=30).update(
        monthly_scans=F('monthly_scans') + 1
    )
    return bool(updated)


def _fire_usage(user, input_tokens: int, output_tokens: int) -> None:
    if not input_tokens and not output_tokens:
        return
    try:
        from emails.tasks import track_llm_usage
        track_llm_usage.defer(user_id=user.pk, input_tokens=input_tokens, output_tokens=output_tokens)
    except Exception as exc:
        logger.error("llm._fire_usage: enqueue failed | user=%s error=%s", user.pk, exc)


def _get_or_create_uncategorized(user):
    from dashboard.models import Category
    category, _ = Category.objects.get_or_create(
        user=user, name='Uncategorized', defaults={'priority': 1},
    )
    return category


def _find_conflicts(user, event_data: dict) -> list:
    from dashboard.models import Event

    conflicts = []
    source_email_id = event_data.get('source_email_id', '')

    if source_email_id:
        by_email = list(
            Event.objects.filter(user=user, source_email_id=source_email_id, status='active')
            .only('pk', 'title', 'start')
        )
        conflicts.extend(by_email)

    title = event_data.get('title', '').strip()
    start_str = event_data.get('start', '')
    if title and start_str:
        try:
            start_dt = parse_datetime(start_str)
            if start_dt:
                by_title = list(
                    Event.objects.filter(
                        user=user, title__iexact=title,
                        start__range=(start_dt - timedelta(hours=1), start_dt + timedelta(hours=1)),
                        status='active',
                    ).exclude(pk__in=[c.pk for c in conflicts])
                    .only('pk', 'title', 'start')
                )
                conflicts.extend(by_title)
        except Exception:
            pass

    return conflicts


def _append_conflict_concern(event_data: dict, conflicts: list) -> dict:
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
    from ..resolver import resolve_category, DISCARD

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
