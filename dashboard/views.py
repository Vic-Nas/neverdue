# dashboard/views.py
import json as _json
import zoneinfo

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .models import Category, Event, FilterRule, Rule
from .ical import build_ics
from accounts.views import GCAL_COLOR_HEX

import logging
logger = logging.getLogger(__name__)


@login_required
def index(request):
    try:
        sort = request.GET.get('sort', 'added')
        order = {'start': 'start', 'added': '-created_at', 'category': 'category__name'}.get(sort, '-created_at')

        events = Event.objects.filter(user=request.user).order_by(order)
        active_events = events.filter(status='active')
        last_event = events.order_by('-created_at').first()
        ctx = {
            'events': events,
            'active_events': active_events,
            'last_event': last_event,
            'sort': sort,
        }
        if not request.user.is_pro:
            scans_used = request.user.monthly_scans
            ctx['scans_used'] = scans_used
            ctx['scans_total'] = 30
            ctx['scans_pct'] = min(int((scans_used / 30) * 100), 100)
        return render(request, 'dashboard/index.html', ctx)
    except Exception:
        logger.exception("Dashboard error for user=%s", request.user.pk)
        return HttpResponse('Dashboard unavailable.', status=500)


@login_required
def event_detail(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        return render(request, 'dashboard/event_detail.html', {'event': event})
    except Exception:
        return HttpResponse('Event unavailable.', status=500)


@login_required
def event_edit(request, pk=None):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user) if pk else None
        categories = Category.objects.filter(user=request.user)
        if request.method == 'POST':
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            start = request.POST.get('start')
            end = request.POST.get('end')
            category_id = request.POST.get('category')
            category = get_object_or_404(Category, pk=category_id, user=request.user) if category_id else None
            recurrence_freq = request.POST.get('recurrence_freq') or None
            recurrence_until = request.POST.get('recurrence_until') or None
            color = request.POST.get('color', '')

            from django.utils.dateparse import parse_datetime
            start_dt = parse_datetime(start)
            end_dt = parse_datetime(end)

            if not start_dt or not end_dt:
                from django.contrib import messages
                messages.error(request, 'Invalid date format.')
                return render(request, 'dashboard/event_edit.html', {'event': event, 'categories': categories})

            user_tz = zoneinfo.ZoneInfo(request.user.timezone or 'UTC')
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=user_tz)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=user_tz)
            start_dt = start_dt.astimezone(zoneinfo.ZoneInfo('UTC'))
            end_dt = end_dt.astimezone(zoneinfo.ZoneInfo('UTC'))

            from django.contrib import messages
            from dashboard.gcal import push_event_to_gcal, update_event_in_gcal

            if event:
                was_pending = event.status == 'pending'
                event.title = title
                event.description = description
                event.start = start_dt
                event.end = end_dt
                event.category = category
                event.recurrence_freq = recurrence_freq
                event.recurrence_until = recurrence_until or None
                event.color = color

                if was_pending:
                    event.status = 'active'
                    event.pending_expires_at = None
                    event.save()
                    result = push_event_to_gcal(request.user, event)
                    if result:
                        html_link, gcal_id = result
                        event.google_event_id = gcal_id
                        event.gcal_link = html_link
                        event.save(update_fields=['google_event_id', 'gcal_link'])
                    else:
                        messages.warning(request, 'Event saved but could not sync to Google Calendar.')
                else:
                    event.save()
                    if not update_event_in_gcal(request.user, event):
                        messages.warning(request, 'Event saved but could not sync to Google Calendar.')
                    if event.google_event_id and event.color:
                        from dashboard.gcal import patch_event_color
                        patch_event_color(request.user, event.google_event_id, event.color)
            else:
                event = Event.objects.create(
                    user=request.user,
                    title=title,
                    description=description,
                    start=start_dt,
                    end=end_dt,
                    category=category,
                    recurrence_freq=recurrence_freq,
                    recurrence_until=recurrence_until or None,
                    color=color,
                )
                result = push_event_to_gcal(request.user, event)
                if result:
                    html_link, gcal_id = result
                    event.google_event_id = gcal_id
                    event.gcal_link = html_link
                    event.save(update_fields=['google_event_id', 'gcal_link'])
                else:
                    messages.warning(request, 'Event saved but could not sync to Google Calendar.')
            return redirect('dashboard:event_detail', pk=event.pk)
        return render(request, 'dashboard/event_edit.html', {'event': event, 'categories': categories})
    except Exception:
        logger.exception("event_edit error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not save event.', status=500)


@login_required
def event_delete(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        if request.method == 'POST':
            event.delete()  # pre_delete signal handles GCal removal
            return redirect('dashboard:index')
        return render(request, 'dashboard/event_delete.html', {'event': event})
    except Exception:
        return HttpResponse('Could not delete event.', status=500)


@login_required
def event_prompt_edit(request, pk):
    """
    User-initiated re-extraction for a single event.

    The event is deleted (signal handles GCal) and its full data is assembled
    with the user's prompt into a new upload job. This is NOT a fix of a
    needs_review job — it is a fresh user-initiated extraction that produces
    a new ScanJob with source='upload'.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return JsonResponse({'ok': False, 'error': 'Prompt is required'})

        event = get_object_or_404(Event, pk=pk, user=request.user)

        # Assemble full context from the event's existing data
        lines = [
            f"Title: {event.title}",
            f"Start: {event.start.isoformat()}",
            f"End: {event.end.isoformat()}",
        ]
        if event.description:
            lines.append(f"Notes: {event.description}")
        if event.recurrence_freq:
            lines.append(f"Recurrence: {event.recurrence_freq}")
            if event.recurrence_until:
                lines.append(f"Recurrence until: {event.recurrence_until}")
        if event.category:
            lines.append(f"Category: {event.category.name}")
        lines.append(f"\nUser instruction: {prompt}")
        full_text = "\n".join(lines)

        # Delete the event — pre_delete signal handles GCal removal
        event.delete()

        # Queue as a new upload job (user-initiated, not a needs_review fix)
        from emails.tasks import process_text_as_upload
        process_text_as_upload.delay(request.user.pk, full_text)

        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("event_prompt_edit error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def events_bulk_action(request):
    """
    Bulk delete or bulk user-initiated re-extraction.

    action='delete'     — delete selected events (signal handles GCal).
    action='reprocess'  — assemble all selected events' data + prompt into
                          one new upload job. Events are deleted first.
                          This is a user-initiated extraction, NOT a
                          needs_review fix — it produces a new ScanJob.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        action = data.get('action')
        ids = [int(i) for i in data.get('ids', [])]
        prompt = data.get('prompt', '').strip()

        if not ids:
            return JsonResponse({'ok': False, 'error': 'No events selected'})

        events = list(Event.objects.filter(pk__in=ids, user=request.user).select_related('category'))
        if not events:
            return JsonResponse({'ok': False, 'error': 'No matching events'})

        if action == 'delete':
            for event in events:
                event.delete()  # pre_delete signal handles GCal removal
            return JsonResponse({'ok': True, 'deleted': len(events)})

        elif action == 'reprocess':
            if not prompt:
                return JsonResponse({'ok': False, 'error': 'Prompt required for reprocess'})

            # Assemble all events' data into one text block
            blocks = []
            for event in events:
                lines = [
                    f"Title: {event.title}",
                    f"Start: {event.start.isoformat()}",
                    f"End: {event.end.isoformat()}",
                ]
                if event.description:
                    lines.append(f"Notes: {event.description}")
                if event.recurrence_freq:
                    lines.append(f"Recurrence: {event.recurrence_freq}")
                    if event.recurrence_until:
                        lines.append(f"Recurrence until: {event.recurrence_until}")
                if event.category:
                    lines.append(f"Category: {event.category.name}")
                blocks.append("\n".join(lines))

            full_text = "\n\n---\n\n".join(blocks) + f"\n\nUser instruction: {prompt}"

            # Delete events — pre_delete signal handles GCal removal
            for event in events:
                event.delete()

            # Queue as a new upload job (user-initiated)
            from emails.tasks import process_text_as_upload
            process_text_as_upload.delay(request.user.pk, full_text)

            return JsonResponse({'ok': True, 'queued': len(events)})

        return JsonResponse({'ok': False, 'error': 'Unknown action'})
    except Exception:
        logger.exception("events_bulk_action error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def queue_job_reprocess(request, pk):
    """
    POST endpoint called from the job detail page when the user submits
    a correction prompt for a needs_review job.

    This is the ONLY entry point that calls reprocess_events — it always
    passes job_pk so the original job is mutated, never a new one created.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.models import ScanJob
        from emails.tasks import reprocess_events

        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        event_ids = [int(i) for i in data.get('event_ids', [])]

        job = get_object_or_404(ScanJob, pk=pk, user=request.user)

        if job.status != ScanJob.STATUS_NEEDS_REVIEW:
            return JsonResponse({'ok': False, 'error': 'Job is not awaiting review'}, status=400)

        if not event_ids:
            # Default to all pending events on this job
            event_ids = list(job.events.filter(status='pending').values_list('pk', flat=True))

        reprocess_events.delay(request.user.pk, event_ids, prompt, job_pk=job.pk)
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("queue_job_reprocess error for user=%s job=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def categories(request):
    try:
        cats = Category.objects.filter(user=request.user).prefetch_related('rules')
        return render(request, 'dashboard/categories.html', {'categories': cats})
    except Exception:
        return HttpResponse('Categories unavailable.', status=500)


@login_required
def category_detail(request, pk):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user)
        events = Event.objects.filter(user=request.user, category=category).order_by('start')
        return render(request, 'dashboard/category_detail.html', {'category': category, 'events': events})
    except Exception:
        return HttpResponse('Category unavailable.', status=500)


@login_required
def category_edit(request, pk=None):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user) if pk else None
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            priority = int(request.POST.get('priority', 1))
            reminders_raw = request.POST.getlist('reminders')
            reminders = [{'minutes': int(m)} for m in reminders_raw if m.isdigit()]

            gcal_color_id = request.POST.get('gcal_color_id', '').strip()
            hex_color = GCAL_COLOR_HEX.get(gcal_color_id, '')

            if category:
                category.name = name
                category.reminders = reminders
                category.priority = priority
                category.gcal_color_id = gcal_color_id
                category.color = hex_color
                category.save()
                from dashboard.tasks import patch_category_colors
                patch_category_colors.delay(request.user.pk, category.pk)
            else:
                category = Category.objects.create(
                    user=request.user,
                    name=name,
                    color=hex_color,
                    priority=priority,
                    gcal_color_id=gcal_color_id,
                    reminders=reminders,
                )

            category.rules.filter(
                rule_type__in=[Rule.TYPE_SENDER, Rule.TYPE_KEYWORD]
            ).delete()
            for sender in request.POST.getlist('rule_sender'):
                sender = sender.strip()
                if sender:
                    Rule.objects.create(
                        user=request.user,
                        rule_type=Rule.TYPE_SENDER,
                        pattern=sender,
                        action=Rule.ACTION_CATEGORIZE,
                        category=category,
                    )
            for keyword in request.POST.getlist('rule_keyword'):
                keyword = keyword.strip()
                if keyword:
                    Rule.objects.create(
                        user=request.user,
                        rule_type=Rule.TYPE_KEYWORD,
                        pattern=keyword,
                        action=Rule.ACTION_CATEGORIZE,
                        category=category,
                    )

            return redirect('dashboard:categories')
        return render(request, 'dashboard/category_edit.html', {'category': category})
    except Exception:
        logger.exception("category_edit error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not save category.', status=500)


@login_required
def category_delete(request, pk):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user)
        if request.method == 'POST':
            category.delete()
            return redirect('dashboard:categories')
        return render(request, 'dashboard/category_delete.html', {'category': category})
    except Exception:
        return HttpResponse('Could not delete category.', status=500)


@login_required
def email_sources(request):
    try:
        filter_rules = FilterRule.objects.filter(user=request.user).order_by('action', 'pattern')
        return render(request, 'dashboard/email_sources.html', {'filter_rules': filter_rules})
    except Exception:
        return HttpResponse('Email sources unavailable.', status=500)


@login_required
def filter_rule_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        action = data.get('action')
        pattern = data.get('pattern', '').strip().lower()

        if action not in ('allow', 'block'):
            return JsonResponse({'ok': False, 'error': 'Invalid action'})
        if not pattern:
            return JsonResponse({'ok': False, 'error': 'Pattern required'})

        rule, created = FilterRule.objects.get_or_create(
            user=request.user,
            pattern=pattern,
            defaults={'action': action},
        )
        if not created:
            return JsonResponse({'ok': False, 'error': 'Pattern already exists'})

        return JsonResponse({'ok': True, 'id': rule.pk})
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def filter_rule_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        FilterRule.objects.filter(pk=pk, user=request.user).delete()
        return JsonResponse({'ok': True})
    except Exception:
        return JsonResponse({'ok': False}, status=500)


@login_required
def upload(request):
    try:
        if not request.user.is_pro:
            return redirect('billing:plans')
        categories = Category.objects.filter(user=request.user)

        if request.method == 'POST':
            from django.contrib import messages

            file = request.FILES.get('file')
            if not file:
                messages.error(request, 'No file selected.')
                return render(request, 'dashboard/upload.html', {'categories': categories})

            context = request.POST.get('context', '').strip()
            content_type = file.content_type
            filename = file.name or ''
            file_bytes = file.read()

            import base64
            from emails.tasks import process_uploaded_file
            file_b64 = base64.b64encode(file_bytes).decode('utf-8')
            process_uploaded_file.delay(request.user.pk, file_b64, content_type, context, filename)

            messages.success(request, 'Your file is being processed. Events will appear shortly.')
            return redirect('dashboard:index')

        return render(request, 'dashboard/upload.html', {'categories': categories})
    except Exception:
        return HttpResponse('Upload unavailable.', status=500)


@login_required
@require_GET
def export_events(request):
    """
    Export selected active events as a .ics file download.

    Query params:
      ?ids=1,2,3   — export specific events by PK
      ?ids=all     — export all active events for the user
    """
    ids_param = request.GET.get('ids', '')

    if ids_param == 'all':
        events = Event.objects.filter(
            user=request.user,
            status='active',
        ).select_related('category').order_by('start')
    else:
        try:
            ids = [int(i) for i in ids_param.split(',') if i.strip()]
        except ValueError:
            return HttpResponse('Invalid ids parameter.', status=400)

        if not ids:
            return HttpResponse('No event IDs provided.', status=400)

        events = Event.objects.filter(
            user=request.user,
            status='active',
            pk__in=ids,
        ).select_related('category').order_by('start')

    if not events.exists():
        return HttpResponse('No active events found for the given IDs.', status=404)

    ics_bytes = build_ics(events)
    response = HttpResponse(ics_bytes, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="neverdue-events.ics"'
    return response


@login_required
@require_GET
def queue(request):
    """Render the queue page. Data is loaded client-side via queue_status."""
    return render(request, 'dashboard/queue.html')


@login_required
@require_GET
def queue_status(request):
    from emails.models import ScanJob
    from dashboard.models import Event
    from django.db.models import Count

    jobs = ScanJob.objects.filter(user=request.user).order_by('-created_at')[:50]

    active_count = sum(1 for j in jobs if j.status in (ScanJob.STATUS_QUEUED, ScanJob.STATUS_PROCESSING))

    job_ids = [j.pk for j in jobs]
    pending_counts = dict(
        Event.objects.filter(scan_job_id__in=job_ids, status='pending')
        .values('scan_job_id')
        .annotate(n=Count('id'))
        .values_list('scan_job_id', 'n')
    )
    active_event_counts = dict(
        Event.objects.filter(scan_job_id__in=job_ids, status='active')
        .values('scan_job_id')
        .annotate(n=Count('id'))
        .values_list('scan_job_id', 'n')
    )

    # Nav badge: number of jobs needing user attention, not number of pending events
    attention_count = sum(1 for j in jobs if j.status == ScanJob.STATUS_NEEDS_REVIEW)

    jobs_data = [
        {
            'id': j.pk,
            'status': j.status,
            'source': j.source,
            'from_address': j.from_address,
            'notes': j.notes,
            'created_at': j.created_at.isoformat(),
            'duration_seconds': round(j.duration_seconds),
            'pending_event_count': pending_counts.get(j.pk, 0),
            'active_event_count': active_event_counts.get(j.pk, 0),
        }
        for j in jobs
    ]

    return JsonResponse({'active_count': active_count, 'attention_count': attention_count, 'jobs': jobs_data})


@login_required
def queue_job_detail(request, pk):
    """
    Detail page for a single ScanJob.
    Shows all events created by the job (active + pending).
    Reprocess is submitted via queue_job_reprocess, not here.
    """
    from emails.models import ScanJob
    try:
        job = get_object_or_404(ScanJob, pk=pk, user=request.user)
        events = Event.objects.filter(scan_job=job).select_related('category').order_by('status', 'start')
        pending_events = [e for e in events if e.status == 'pending']
        active_events = [e for e in events if e.status == 'active']
        return render(request, 'dashboard/queue_job_detail.html', {
            'job': job,
            'pending_events': pending_events,
            'active_events': active_events,
        })
    except Exception:
        return HttpResponse('Job unavailable.', status=500)


@login_required
def rules(request):
    try:
        user_rules = Rule.objects.filter(user=request.user).select_related('category').order_by('rule_type', 'created_at')
        categories = Category.objects.filter(user=request.user).order_by('name')
        return render(request, 'dashboard/rules.html', {
            'rules': user_rules,
            'categories': categories,
        })
    except Exception:
        return HttpResponse('Rules unavailable.', status=500)


@login_required
def rule_add(request):
    import json as _rule_json
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _rule_json.loads(request.body)
        rule_type = data.get('rule_type', '').strip()

        if rule_type not in (Rule.TYPE_SENDER, Rule.TYPE_KEYWORD, Rule.TYPE_PROMPT):
            return JsonResponse({'ok': False, 'error': 'Invalid rule type'})

        if rule_type == Rule.TYPE_PROMPT:
            prompt_text = data.get('prompt_text', '').strip()
            if not prompt_text:
                return JsonResponse({'ok': False, 'error': 'Prompt text required'})
            pattern = data.get('pattern', '').strip()
            rule = Rule.objects.create(
                user=request.user,
                rule_type=Rule.TYPE_PROMPT,
                pattern=pattern,
                prompt_text=prompt_text,
            )
            return JsonResponse({'ok': True, 'id': rule.pk})

        pattern = data.get('pattern', '').strip()
        action = data.get('action', '').strip()
        if not pattern:
            return JsonResponse({'ok': False, 'error': 'Pattern required'})
        if action not in (Rule.ACTION_CATEGORIZE, Rule.ACTION_DISCARD):
            return JsonResponse({'ok': False, 'error': 'Invalid action'})

        category = None
        if action == Rule.ACTION_CATEGORIZE:
            category_id = data.get('category_id')
            if category_id:
                category = get_object_or_404(Category, pk=category_id, user=request.user)

        rule = Rule.objects.create(
            user=request.user,
            rule_type=rule_type,
            pattern=pattern,
            action=action,
            category=category,
        )
        return JsonResponse({'ok': True, 'id': rule.pk})
    except Exception:
        logger.exception("rule_add error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def rule_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        Rule.objects.filter(pk=pk, user=request.user).delete()
        return JsonResponse({'ok': True})
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)