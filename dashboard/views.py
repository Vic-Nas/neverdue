# dashboard/views.py
import json as _json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .models import Category, Event, Rule
from .ical import build_ics


@login_required
def index(request):
    try:
        sort = request.GET.get('sort', 'start')
        order = {'start': 'start', 'added': '-created_at', 'category': 'category__name'}.get(sort, 'start')

        events = Event.objects.filter(user=request.user).order_by(order)
        active_events = events.filter(status='active')
        pending_events = Event.objects.filter(user=request.user, status='pending').order_by('pending_expires_at', 'created_at')
        last_event = events.order_by('-created_at').first()
        ctx = {
            'events': events,
            'active_events': active_events,
            'pending_events': pending_events,
            'last_event': last_event,
            'sort': sort,
        }
        if not request.user.is_pro:
            scans_used = request.user.monthly_scans
            ctx['scans_used'] = scans_used
            ctx['scans_total'] = 30
            ctx['scans_pct'] = min(int((scans_used / 30) * 100), 100)
        return render(request, 'dashboard/index.html', ctx)
    except Exception as e:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Dashboard error: {e}")
        logger.error(traceback.format_exc())
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

            from django.utils.dateparse import parse_datetime
            start_dt = parse_datetime(start)
            end_dt = parse_datetime(end)

            if not start_dt or not end_dt:
                from django.contrib import messages
                messages.error(request, 'Invalid date format.')
                return render(request, 'dashboard/event_edit.html', {'event': event, 'categories': categories})

            if event:
                event.title = title
                event.description = description
                event.start = start_dt
                event.end = end_dt
                event.category = category
                event.recurrence_freq = recurrence_freq
                event.recurrence_until = recurrence_until or None
                if event.status == 'pending':
                    event.status = 'active'
                    event.pending_expires_at = None
                event.save()
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
                )
            return redirect('dashboard:event_detail', pk=event.pk)
        return render(request, 'dashboard/event_edit.html', {'event': event, 'categories': categories})
    except Exception as e:
        import traceback
        import logging
        logging.getLogger(__name__).error(traceback.format_exc())
        return HttpResponse(f'Could not save event: {e}', status=500)


@login_required
def event_delete(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        if request.method == 'POST':
            if event.google_event_id:
                try:
                    from accounts.utils import get_valid_token
                    import requests as http
                    token = get_valid_token(request.user)
                    http.delete(
                        f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event.google_event_id}',
                        headers={'Authorization': f'Bearer {token}'},
                    )
                except Exception:
                    pass
            event.delete()
            return redirect('dashboard:index')
        return render(request, 'dashboard/event_delete.html', {'event': event})
    except Exception:
        return HttpResponse('Could not delete event.', status=500)


@login_required
def event_prompt_edit(request, pk):
    """
    POST: take the current event's full context + user prompt,
    delete the event, queue re-extraction. User is redirected immediately.
    The event will reappear as pending once the worker finishes.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        import json as _json
        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return JsonResponse({'ok': False, 'error': 'Prompt is required'})

        event = get_object_or_404(Event, pk=pk, user=request.user)

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

        if event.google_event_id:
            try:
                from accounts.utils import get_valid_token
                import requests as http
                token = get_valid_token(request.user)
                http.delete(
                    f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event.google_event_id}',
                    headers={'Authorization': f'Bearer {token}'},
                    timeout=10,
                )
            except Exception:
                pass
        event.delete()

        from emails.tasks import reprocess_events
        reprocess_events.delay(request.user.pk, [], full_text)

        return JsonResponse({'ok': True})

    except Exception:
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def events_bulk_action(request):
    """
    POST endpoint for bulk delete or bulk reprocess with a prompt.
    Body: { "action": "delete"|"reprocess", "ids": [1,2,3], "prompt": "..." }
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

        owned_ids = list(Event.objects.filter(pk__in=ids, user=request.user).values_list('pk', flat=True))
        if not owned_ids:
            return JsonResponse({'ok': False, 'error': 'No matching events'})

        if action == 'delete':
            events = Event.objects.filter(pk__in=owned_ids)
            for event in events:
                if event.google_event_id:
                    try:
                        from accounts.utils import get_valid_token
                        import requests as http
                        token = get_valid_token(request.user)
                        http.delete(
                            f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event.google_event_id}',
                            headers={'Authorization': f'Bearer {token}'},
                            timeout=10,
                        )
                    except Exception:
                        pass
            events.delete()
            return JsonResponse({'ok': True, 'deleted': len(owned_ids)})

        elif action == 'reprocess':
            if not prompt:
                return JsonResponse({'ok': False, 'error': 'Prompt required for reprocess'})
            from emails.tasks import reprocess_events
            reprocess_events.delay(request.user.pk, owned_ids, prompt)
            return JsonResponse({'ok': True, 'queued': len(owned_ids)})

        return JsonResponse({'ok': False, 'error': 'Unknown action'})
    except Exception:
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
            color = request.POST.get('color', '').strip()
            reminders_raw = request.POST.getlist('reminders')
            reminders = [{'minutes': int(m)} for m in reminders_raw if m.isdigit()]

            if category:
                category.name = name
                category.color = color
                category.reminders = reminders
                category.save()
            else:
                category = Category.objects.create(
                    user=request.user,
                    name=name,
                    color=color,
                    reminders=reminders,
                )

            category.rules.all().delete()
            senders = request.POST.getlist('rule_sender')
            keywords = request.POST.getlist('rule_keyword')

            for sender, keyword in zip(senders, keywords):
                sender = sender.strip()
                keyword = keyword.strip()
                if sender:
                    Rule.objects.get_or_create(
                        user=request.user,
                        pattern=sender,
                        defaults={'action': 'categorize', 'category': category},
                    )
                if keyword:
                    Rule.objects.get_or_create(
                        user=request.user,
                        pattern=keyword,
                        defaults={'action': 'categorize', 'category': category},
                    )

            return redirect('dashboard:categories')
        return render(request, 'dashboard/category_edit.html', {'category': category})
    except Exception as e:
        import traceback, logging
        logging.getLogger(__name__).error(traceback.format_exc())
        return HttpResponse(f'Could not save category: {e}', status=500)


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
        from .models import FilterRule
        filter_rules = FilterRule.objects.filter(user=request.user).order_by('action', 'pattern')
        return render(request, 'dashboard/email_sources.html', {'filter_rules': filter_rules})
    except Exception:
        return HttpResponse('Email sources unavailable.', status=500)


@login_required
def filter_rule_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from .models import FilterRule
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
        from .models import FilterRule
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
    """
    Returns the count of active (queued + processing) ScanJobs for the current user,
    plus the jobs themselves for the queue page.
    Used by the nav badge (polling) and the queue page.
    """
    from emails.models import ScanJob

    jobs = ScanJob.objects.filter(
        user=request.user,
    ).order_by('-created_at')[:50]  # cap at 50 for the page view

    active_count = sum(1 for j in jobs if j.status in (ScanJob.STATUS_QUEUED, ScanJob.STATUS_PROCESSING))

    jobs_data = [
        {
            'id': j.pk,
            'status': j.status,
            'source': j.source,
            'summary': j.summary,
            'created_at': j.created_at.isoformat(),
            'duration_seconds': round(j.duration_seconds),
        }
        for j in jobs
    ]

    return JsonResponse({'active_count': active_count, 'jobs': jobs_data})
