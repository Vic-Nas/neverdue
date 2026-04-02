# project/staff/actions.py
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib import messages

from emails.models import ScanJob
from emails.tasks import _retry_jobs
from .dashboard import staff_required


def _parse_pks(request) -> list:
    if request.content_type == "application/json":
        data = json.loads(request.body)
        return data.get("pks", [])
    return request.POST.getlist("pks")


@staff_required
@require_POST
def staff_retry_jobs(request):
    reason  = request.POST.get('reason')
    job_ids = request.POST.getlist('job_ids')

    if reason:
        jobs = list(ScanJob.objects.filter(status=ScanJob.STATUS_FAILED, failure_reason=reason))
    elif job_ids:
        jobs = list(ScanJob.objects.filter(pk__in=job_ids, status=ScanJob.STATUS_FAILED))
    else:
        jobs = []

    _retry_jobs(jobs)
    messages.success(request, f'Re-enqueued {len(jobs)} job(s).')
    return redirect('staff_dashboard')


@staff_required
@require_POST
def staff_retry_single(request, pk):
    job = get_object_or_404(ScanJob, pk=pk, status=ScanJob.STATUS_FAILED)
    _retry_jobs([job])
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'count': 1})
    messages.success(request, f'Job {pk} re-enqueued.')
    return redirect('staff_dashboard')


@staff_required
@require_POST
def staff_delete_single(request, pk):
    job = get_object_or_404(ScanJob, pk=pk)
    job.delete()
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    messages.success(request, f'Job {pk} deleted.')
    return redirect('staff_dashboard')


@staff_required
@require_POST
def staff_bulk_retry(request):
    pks = _parse_pks(request)
    jobs = list(ScanJob.objects.filter(pk__in=pks, status=ScanJob.STATUS_FAILED))
    _retry_jobs(jobs)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'count': len(jobs)})
    messages.success(request, f'Re-enqueued {len(jobs)} job(s).')
    return redirect('staff_dashboard')


@staff_required
@require_POST
def staff_bulk_delete(request):
    pks = _parse_pks(request)
    count, _ = ScanJob.objects.filter(pk__in=pks).delete()

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'count': count})
    messages.success(request, f'Deleted {count} job(s).')
    return redirect('staff_dashboard')
