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
        events = Event.objects.filter(
            user=request.user,
        ).select_related('category').order_by('start')
        active_events = events.filter(status='active')
        return render(request, 'dashboard/index.html', {
            'active_events': active_events,
        })
    except Exception:
        logger.exception("index error for user=%s", request.user.pk)
        return HttpResponse('Dashboard unavailable.', status=500)


@login_required
def event_detail(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        return render(request, 'dashboard/event_detail.html', {'event': event})
    except Exception:
        logger.exception("event_detail error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Event unavailable.', status=500)


@login_required
def event_edit(request, pk=None):
    try:
        from django.utils.dateparse import parse_datetime
        if pk:
            event = get_object_or_404(Event, pk=pk, user=request.user)
        else:
            event = None

        categories = Category.objects.filter(user=request.user).order_by('name')

        if request.method == 'POST':
            data = _json.loads(request.body)
            title = data.get('title', '').strip()
            start_str = data.get('start', '')
            end_str = data.get('end', '')
            description = data.get('description', '').strip()
            category_id = data.get('category_id')
            color = data.get('color', '').strip()

            if not title or not start_str or not end_str:
                return JsonResponse({'ok': False, 'error': 'Title, start, and end are required.'}, status=400)

            start = parse_datetime(start_str)
            end = parse_datetime(end_str)
            if not start or not end:
                return JsonResponse({'ok': False, 'error': 'Invalid date format.'}, status=400)

            category = None
            if category_id:
                try:
                    category = Category.objects.get(pk=category_id, user=request.user)
                except Category.DoesNotExist:
                    return JsonResponse({'ok': False, 'error': 'Category not found.'}, status=400)

            was_pending = event.status == 'pending' if event else False

            if event is None:
                event = Event(user=request.user)

            event.title = title
            event.start = start
            event.end = end
            event.description = description
            event.category = category
            event.color = color

            if was_pending:
                event.status = 'active'
                event.pending_expires_at = None

            event.save()

            return JsonResponse({'ok': True, 'pk': event.pk})

        return render(request, 'dashboard/event_edit.html', {
            'event': event,
            'categories': categories,
        })
    except Exception:
        logger.exception("event_edit error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not save event.', status=500)


@login_required
def event_delete(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        event.delete()
        return redirect('dashboard:index')
    except Exception:
        logger.exception("event_delete error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not delete event.', status=500)


@login_required
def event_prompt_edit(request, pk):
    """
    Delete an event and re-extract with a user-supplied prompt.
    Creates a new ScanJob with source='upload'.
    This is a user-initiated re-extraction — NOT a needs_review fix.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.tasks import process_text_as_upload
        event = get_object_or_404(Event, pk=pk, user=request.user)
        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return JsonResponse({'ok': False, 'error': 'Prompt is required.'}, status=400)

        lines = [
            f"Title: {event.title}",
            f"Start: {event.start.isoformat()}",
            f"End: {event.end.isoformat()}",
        ]
        if event.description:
            lines.append(f"Notes: {event.description}")
        if event.category:
            lines.append(f"Category: {event.category.name}")

        full_text = "\n".join(lines) + f"\n\nUser instruction: {prompt}"

        event.delete()
        process_text_as_upload.delay(request.user.pk, full_text)
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("event_prompt_edit error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def events_bulk_action(request):
    """
    Bulk delete events with optional re-extraction prompt.
    When a prompt is supplied, creates a new upload ScanJob —
    needs_review fix — it produces a new ScanJob.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.tasks import process_text_as_upload
        data = _json.loads(request.body)
        event_ids = [int(i) for i in data.get('event_ids', [])]
        prompt = data.get('prompt', '').strip()
        action = data.get('action', 'delete')

        events = Event.objects.filter(pk__in=event_ids, user=request.user)

        if action == 'delete' or not prompt:
            count = events.count()
            events.delete()
            return JsonResponse({'ok': True, 'deleted': count})

        # Re-extract: serialise events, delete them, dispatch upload task
        blocks = []
        for e in events:
            lines = [
                f"Title: {e.title}",
                f"Start: {e.start.isoformat()}",
                f"End: {e.end.isoformat()}",
            ]
            if e.description:
                lines.append(f"Notes: {e.description}")
            if e.category:
                lines.append(f"Category: {e.category.name}")
            blocks.append("\n".join(lines))

        full_text = "\n\n---\n\n".join(blocks) + f"\n\nUser instruction: {prompt}"

        events.delete()
        process_text_as_upload.delay(request.user.pk, full_text)
        return JsonResponse({'ok': True, 'queued': len(blocks)})
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
def queue_job_retry(request, pk):
    """
    POST endpoint to retry a single failed job from the job detail page.
    Resets the job to queued and re-enqueues it.
    Only works on failed jobs.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.models import ScanJob
        from emails.tasks import _reenqueue_jobs

        job = get_object_or_404(ScanJob, pk=pk, user=request.user)

        if job.status != ScanJob.STATUS_FAILED:
            return JsonResponse({'ok': False, 'error': 'Job is not failed'}, status=400)

        _reenqueue_jobs([job])
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("queue_job_retry error for user=%s job=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def categories(request):
    try:
        cats = Category.objects.filter(user=request.user).order_by('name')
        return render(request, 'dashboard/categories.html', {'categories': cats})
    except Exception:
        logger.exception("categories error for user=%s", request.user.pk)
        return HttpResponse('Categories unavailable.', status=500)


@login_required
def category_detail(request, pk):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user)
        return render(request, 'dashboard/category_detail.html', {'category': category})
    except Exception:
        logger.exception("category_detail error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Category unavailable.', status=500)


@login_required
def category_edit(request, pk=None):
    try:
        if pk:
            category = get_object_or_404(Category, pk=pk, user=request.user)
        else:
            category = None

        if request.method == 'POST':
            data = _json.loads(request.body)
            name = data.get('name', '').strip()
            color = data.get('color', '').strip()
            gcal_color_id = data.get('gcal_color_id', '').strip()
            priority = data.get('priority', 1)

            if not name:
                return JsonResponse({'ok': False, 'error': 'Name is required.'}, status=400)

            if category is None:
                category = Category(user=request.user)

            category.name = name
            category.color = color
            category.gcal_color_id = gcal_color_id
            category.priority = priority
            category.save()

            if gcal_color_id:
                from dashboard.tasks import patch_category_colors
                patch_category_colors.delay(request.user.pk, category.pk)

            return JsonResponse({'ok': True, 'pk': category.pk})

        return render(request, 'dashboard/category_edit.html', {
            'category': category,
            'gcal_color_hex': GCAL_COLOR_HEX,
        })
    except Exception:
        logger.exception("category_edit error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not save category.', status=500)


@login_required
def category_delete(request, pk):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user)
        category.delete()
        return redirect('dashboard:categories')
    except Exception:
        logger.exception("category_delete error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not delete category.', status=500)


@login_required
def email_sources(request):
    try:
        return render(request, 'dashboard/email_sources.html')
    except Exception:
        logger.exception("email_sources error for user=%s", request.user.pk)
        return HttpResponse('Email sources unavailable.', status=500)


@login_required
def filter_rule_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        pattern = data.get('pattern', '').strip()
        action = data.get('action', '').strip()
        category_id = data.get('category_id')

        if not pattern or not action:
            return JsonResponse({'ok': False, 'error': 'Pattern and action are required.'}, status=400)

        category = None
        if category_id:
            category = get_object_or_404(Category, pk=category_id, user=request.user)

        FilterRule.objects.create(
            user=request.user,
            pattern=pattern,
            action=action,
            category=category,
        )
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("filter_rule_add error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def filter_rule_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        rule = get_object_or_404(FilterRule, pk=pk, user=request.user)
        rule.delete()
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("filter_rule_delete error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False}, status=500)


@login_required
def upload(request):
    try:
        import base64
        if request.method == 'POST':
            from emails.tasks import process_uploaded_file
            uploaded = request.FILES.get('file')
            context = request.POST.get('context', '').strip()
            if not uploaded:
                return JsonResponse({'ok': False, 'error': 'No file provided.'}, status=400)
            content_type = uploaded.content_type or 'application/octet-stream'
            file_bytes = uploaded.read()
            file_b64 = base64.b64encode(file_bytes).decode('utf-8')
            filename = uploaded.name or ''
            process_uploaded_file.delay(request.user.pk, file_b64, content_type, context, filename)
            return JsonResponse({'ok': True})
        return render(request, 'dashboard/upload.html')
    except Exception:
        logger.exception("upload error for user=%s", request.user.pk)
        return HttpResponse('Upload unavailable.', status=500)


def export_events(request):
    """
    Export selected active events as a .ics file download.

    Query params:
      ?ids=1,2,3  — export specific event IDs (must belong to request.user)
      ?ids=all     — export all active events for the user
    """
    try:
        ids_param = request.GET.get('ids', '')
        if ids_param == 'all':
            events = Event.objects.filter(
                user=request.user,
                status='active',
            ).select_related('category')
        elif ids_param:
            try:
                id_list = [int(i) for i in ids_param.split(',') if i.strip()]
            except ValueError:
                return HttpResponse('Invalid ids parameter.', status=400)
            if not id_list:
                return HttpResponse('No event IDs provided.', status=400)
            events = Event.objects.filter(
                pk__in=id_list,
                user=request.user,
                status='active',
            ).select_related('category')
        else:
            return HttpResponse('No event IDs provided.', status=400)

        if not events.exists():
            return HttpResponse('No active events found for the given IDs.', status=404)

        ics_content = build_ics(events)
        response = HttpResponse(ics_content, content_type='text/calendar')
        response['Content-Disposition'] = 'attachment; filename="neverdue-events.ics"'
        return response
    except Exception:
        logger.exception("export_events error for user=%s", request.user.pk)
        return HttpResponse('Export unavailable.', status=500)


@login_required
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

    # Nav badge: number of jobs needing user attention
    # Includes needs_review (user must act) and failed (user should be aware)
    attention_count = sum(
        1 for j in jobs
        if j.status in (ScanJob.STATUS_NEEDS_REVIEW, ScanJob.STATUS_FAILED)
    )

    jobs_data = [
        {
            'id': j.pk,
            'status': j.status,
            'source': j.source,
            'from_address': j.from_address,
            'notes': j.notes,
            'failure_reason': j.failure_reason,
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
        logger.exception("queue_job_detail error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Job unavailable.', status=500)


@login_required
def rules(request):
    try:
        rules_qs = Rule.objects.filter(user=request.user).select_related('category').order_by('rule_type', 'created_at')
        categories = Category.objects.filter(user=request.user).order_by('name')
        return render(request, 'dashboard/rules.html', {'rules': rules_qs, 'categories': categories})
    except Exception:
        logger.exception("rules error for user=%s", request.user.pk)
        return HttpResponse('Rules unavailable.', status=500)


@login_required
def rule_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        rule_type = data.get('rule_type', '').strip()
        pattern = data.get('pattern', '').strip()
        action = data.get('action', '').strip()
        category_id = data.get('category_id')

        if not rule_type or not action:
            return JsonResponse({'ok': False, 'error': 'Rule type and action are required.'}, status=400)

        if action == 'categorize' and not category_id:
            return JsonResponse({'ok': False, 'error': 'A category is required for categorize action.'}, status=400)

        category = None
        if category_id:
            category = get_object_or_404(Category, pk=category_id, user=request.user)

        Rule.objects.create(
            user=request.user,
            rule_type=rule_type,
            pattern=pattern,
            action=action,
            category=category,
        )
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("rule_add error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def rule_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        rule = get_object_or_404(Rule, pk=pk, user=request.user)
        rule.delete()
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("rule_delete error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)
