# dashboard/views.py
import base64
import json as _json
import zoneinfo

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .models import Category, Event, Rule
from .ical import build_ics
from accounts.views import GCAL_COLOR_HEX

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_reprocess_text(events_qs, prompt: str) -> str:
    """
    Serialise a queryset of events + a user instruction into the text blob
    sent to process_text_as_upload. Used by event_prompt_edit and events_bulk_action.
    """
    blocks = [e.serialize_as_text() for e in events_qs]
    return "\n\n---\n\n".join(blocks) + f"\n\nUser instruction: {prompt}"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

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
        from emails.models import ScanJob
        event = get_object_or_404(Event, pk=pk, user=request.user)
        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return JsonResponse({'ok': False, 'error': 'Prompt is required.'}, status=400)

        full_text = _build_reprocess_text([event], prompt)
        event.delete()

        job = ScanJob.objects.create(
            user=request.user,
            source=ScanJob.SOURCE_UPLOAD,
            status=ScanJob.STATUS_QUEUED,
            upload_text=full_text,
        )
        process_text_as_upload.defer(job_id=job.id, user_id=request.user.pk, text=full_text)
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("event_prompt_edit error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def events_bulk_action(request):
    """
    Bulk delete events with optional re-extraction prompt.
    When a prompt is supplied, creates a new upload ScanJob for re-extraction.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.tasks import process_text_as_upload
        from emails.models import ScanJob
        data = _json.loads(request.body)
        event_ids = [int(i) for i in data.get('event_ids', [])]
        prompt = data.get('prompt', '').strip()
        action = data.get('action', 'delete')

        events = Event.objects.filter(pk__in=event_ids, user=request.user)

        if action == 'delete' or not prompt:
            count = events.count()
            events.delete()
            return JsonResponse({'ok': True, 'deleted': count})

        full_text = _build_reprocess_text(events, prompt)
        events.delete()

        job = ScanJob.objects.create(
            user=request.user,
            source=ScanJob.SOURCE_UPLOAD,
            status=ScanJob.STATUS_QUEUED,
            upload_text=full_text,
        )
        process_text_as_upload.defer(job_id=job.id, user_id=request.user.pk, text=full_text)
        return JsonResponse({'ok': True, 'queued': len(event_ids)})
    except Exception:
        logger.exception("events_bulk_action error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def queue_job_reprocess(request, pk):
    """
    POST endpoint called from the job detail page when the user submits
    a correction prompt for a needs_review job.

    This is the ONLY entry point that calls reprocess_events — it always
    passes the existing job's pk so no new job is created.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.tasks import reprocess_events
        from emails.models import ScanJob
        job = get_object_or_404(ScanJob, pk=pk, user=request.user, status=ScanJob.STATUS_NEEDS_REVIEW)
        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        event_ids = data.get('event_ids', [])
        reprocess_events.defer(user_id=request.user.pk, event_ids=event_ids, prompt=prompt, job_pk=job.pk)
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("queue_job_reprocess error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def queue_job_retry(request, pk):
    """
    POST endpoint to manually retry a failed job.
    Resets the job to queued and dispatches via _dispatch_job.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.models import ScanJob
        from emails.tasks import _retry_jobs
        job = get_object_or_404(ScanJob, pk=pk, user=request.user, status=ScanJob.STATUS_FAILED)
        _retry_jobs([job])
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("queue_job_retry error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def upload(request):
    try:
        from emails.tasks import process_uploaded_file
        from emails.models import ScanJob
        if request.method == 'POST':
            uploaded = request.FILES.get('file')
            context = request.POST.get('context', '').strip()
            if not uploaded:
                return JsonResponse({'ok': False, 'error': 'No file provided.'}, status=400)
            content_type = uploaded.content_type or 'application/octet-stream'
            file_bytes = uploaded.read()
            file_b64 = base64.b64encode(file_bytes).decode('utf-8')
            filename = uploaded.name or ''
            job = ScanJob.objects.create(
                user=request.user,
                source=ScanJob.SOURCE_UPLOAD,
                status=ScanJob.STATUS_QUEUED,
                file_b64=file_b64,
                media_type=content_type,
                upload_context=context,
                filename=filename,
            )
            process_uploaded_file.defer(
                job_id=job.id,
                user_id=request.user.pk,
                file_b64=file_b64,
                media_type=content_type,
                context=context,
                filename=filename,
            )
            return JsonResponse({'ok': True})
        return render(request, 'dashboard/upload.html', {
            'categories': Category.objects.filter(user=request.user).order_by('name'),
        })
    except Exception:
        logger.exception("upload error for user=%s", request.user.pk)
        return HttpResponse('Upload unavailable.', status=500)


def export_events(request):
    """
    Export selected active events as a .ics file download.

    Query params:
      ?ids=1,2,3  — export specific event IDs (must belong to request.user)
      ?ids=all    — export all active events for the user
    """
    try:
        ids_param = request.GET.get('ids', '')
        if ids_param == 'all':
            events = Event.objects.filter(
                user=request.user, status='active',
            ).select_related('category')
        elif ids_param:
            try:
                id_list = [int(i) for i in ids_param.split(',') if i.strip()]
            except ValueError:
                return HttpResponse('Invalid ids parameter.', status=400)
            if not id_list:
                return HttpResponse('No event IDs provided.', status=400)
            events = Event.objects.filter(
                pk__in=id_list, user=request.user, status='active',
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
    from django.db.models import Count

    jobs = ScanJob.objects.filter(user=request.user).order_by('-created_at')[:50]
    active_count = sum(1 for j in jobs if j.status in (ScanJob.STATUS_QUEUED, ScanJob.STATUS_PROCESSING))

    job_ids = [j.pk for j in jobs]
    pending_counts = dict(
        Event.objects.filter(scan_job_id__in=job_ids, status='pending')
        .values('scan_job_id').annotate(n=Count('id')).values_list('scan_job_id', 'n')
    )
    active_event_counts = dict(
        Event.objects.filter(scan_job_id__in=job_ids, status='active')
        .values('scan_job_id').annotate(n=Count('id')).values_list('scan_job_id', 'n')
    )

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
        prompt_text = data.get('prompt_text', '').strip()

        if not rule_type:
            return JsonResponse({'ok': False, 'error': 'Rule type is required.'}, status=400)

        if rule_type == Rule.TYPE_PROMPT:
            if not prompt_text:
                return JsonResponse({'ok': False, 'error': 'Prompt text is required.'}, status=400)
            Rule.objects.create(
                user=request.user,
                rule_type=rule_type,
                pattern=pattern,
                prompt_text=prompt_text,
            )
            return JsonResponse({'ok': True})

        # sender / keyword rules require an action
        if not action:
            return JsonResponse({'ok': False, 'error': 'Action is required.'}, status=400)

        # allow/block are only valid for sender rules
        if action in (Rule.ACTION_ALLOW, Rule.ACTION_BLOCK) and rule_type != Rule.TYPE_SENDER:
            return JsonResponse(
                {'ok': False, 'error': 'Allow and block actions are only valid for sender rules.'},
                status=400,
            )

        if action == Rule.ACTION_CATEGORIZE and not category_id:
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
        events = Event.objects.filter(category=category, user=request.user, status='active').order_by('start')
        return render(request, 'dashboard/category_detail.html', {
            'category': category,
            'events': events,
            'gcal_color_hex': GCAL_COLOR_HEX,
        })
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
            priority = data.get('priority', 2)
            gcal_color_id = data.get('gcal_color_id')
            reminders = data.get('reminders', [])

            if not name:
                return JsonResponse({'ok': False, 'error': 'Name is required.'}, status=400)

            if category is None:
                category = Category(user=request.user)

            old_color = category.gcal_color_id
            category.name = name
            category.priority = priority
            category.gcal_color_id = gcal_color_id
            category.reminders = reminders
            category.save()

            # Patch calendar colors if changed
            if gcal_color_id != old_color:
                from dashboard.tasks import patch_category_colors
                patch_category_colors.defer(user_id=request.user.pk, category_id=category.pk)

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
        categories_url = '/dashboard/categories/'
        if request.method == 'POST':
            category.delete()
            return redirect('dashboard:categories')
        return render(request, 'dashboard/category_delete.html', {
            'category': category,
            'categories_url': categories_url,
        })
    except Exception:
        logger.exception("category_delete error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not delete category.', status=500)