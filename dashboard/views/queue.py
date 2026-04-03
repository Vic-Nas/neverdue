# dashboard/views/queue.py
import json as _json
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from dashboard.models import Event

logger = logging.getLogger(__name__)


@login_required
def queue(request):
    return render(request, 'dashboard/queue.html')


@login_required
@require_GET
def queue_status(request):
    from emails.models import ScanJob

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
        1 for j in jobs if j.status in (ScanJob.STATUS_NEEDS_REVIEW, ScanJob.STATUS_FAILED)
    )

    jobs_data = [
        {
            'id': j.pk, 'status': j.status, 'source': j.source,
            'from_address': j.from_address, 'notes': j.notes,
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
    from emails.models import ScanJob
    try:
        job = get_object_or_404(ScanJob, pk=pk, user=request.user)
        events = Event.objects.filter(scan_job=job).select_related('category').order_by('status', 'start')
        pending_events = [e for e in events if e.status == 'pending']
        active_events = [e for e in events if e.status == 'active']
        return render(request, 'dashboard/queue_job_detail.html', {
            'job': job, 'pending_events': pending_events, 'active_events': active_events,
        })
    except Exception:
        logger.exception("queue_job_detail error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Job unavailable.', status=500)


@login_required
def queue_job_reprocess(request, pk):
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
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.models import ScanJob
        from emails.tasks import _retry_jobs
        job = get_object_or_404(
            ScanJob, pk=pk, user=request.user,
            status__in=[ScanJob.STATUS_FAILED, ScanJob.STATUS_NEEDS_REVIEW],
        )
        _retry_jobs([job])
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("queue_job_retry error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)
