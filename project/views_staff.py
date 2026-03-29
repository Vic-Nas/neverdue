# project/views_staff.py
import datetime
import json
from functools import wraps

from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib import messages

from accounts.models import MonthlyUsage, User
from emails.models import ScanJob
from emails.tasks import _reenqueue_jobs

INPUT_CPM = 3.00
OUTPUT_CPM = 15.00

FAILURE_COLORS = {
    'llm_error':      '#dc2626',
    'scan_limit':     '#d97706',
    'pro_required':   '#2563eb',
    'internal_error': '#7c3aed',
}


def staff_required(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect('/dashboard/')
        return view(request, *args, **kwargs)
    return inner


def _cost(inp, out):
    return round((inp / 1_000_000) * INPUT_CPM + (out / 1_000_000) * OUTPUT_CPM, 4)


def _date_range(days=30):
    today = timezone.now().date()
    return [(today - datetime.timedelta(days=i)) for i in range(days - 1, -1, -1)]


def _parse_pks(request) -> list:
    """Parse a list of job PKs from either JSON body or form POST."""
    if request.content_type == "application/json":
        import json as _json
        data = _json.loads(request.body)
        return data.get("pks", [])
    return request.POST.getlist("pks")


@staff_required
def staff_dashboard(request):
    now   = timezone.now()
    today = now.date()

    # ── Summary counts ──────────────────────────────────────────────────────
    counts = ScanJob.objects.aggregate(
        total_today  = Count('pk', filter=Q(created_at__date=today)),
        done_today   = Count('pk', filter=Q(status=ScanJob.STATUS_DONE, updated_at__date=today)),
        failed       = Count('pk', filter=Q(status=ScanJob.STATUS_FAILED)),
        needs_review = Count('pk', filter=Q(status=ScanJob.STATUS_NEEDS_REVIEW)),
        in_flight    = Count('pk', filter=Q(status__in=[ScanJob.STATUS_QUEUED, ScanJob.STATUS_PROCESSING])),
        total_all    = Count('pk'),
    )

    # ── LLM cost this month ─────────────────────────────────────────────────
    snap = MonthlyUsage.objects.filter(year=today.year, month=today.month).aggregate(
        inp=Sum('input_tokens'), out=Sum('output_tokens')
    )
    roll = User.objects.aggregate(inp=Sum('monthly_input_tokens'), out=Sum('monthly_output_tokens'))
    month_input  = (snap['inp'] or 0) + (roll['inp'] or 0)
    month_output = (snap['out'] or 0) + (roll['out'] or 0)
    month_cost   = _cost(month_input, month_output)

    # ── Historical monthly cost (last 6 months) ─────────────────────────────
    hist = list(
        MonthlyUsage.objects
        .values('year', 'month')
        .annotate(inp=Sum('input_tokens'), out=Sum('output_tokens'))
        .order_by('-year', '-month')[:6]
    )
    hist.reverse()
    monthly_cost_labels = [f"{r['year']}-{r['month']:02d}" for r in hist]
    monthly_cost_values = [_cost(r['inp'] or 0, r['out'] or 0) for r in hist]

    # ── 30-day attempt breakdown by status and failure_reason ──────────────
    # Query JobAttemptLog instead of ScanJob.status='failed' for accurate metrics.
    # ScanJob.status only shows final state; when a job is retried and succeeds,
    # its failure disappears. JobAttemptLog records every attempt permanently.
    from emails.models import JobAttemptLog
    
    cutoff     = now - datetime.timedelta(days=29)
    date_range = _date_range(30)
    date_strs  = [d.isoformat() for d in date_range]

    # Query attempts grouped by attempt date, status, and failure reason
    attempt_qs = (
        JobAttemptLog.objects
        .filter(created_at__gte=cutoff)
        .extra(select={'day': 'DATE(created_at)'})
        .values('day', 'status', 'failure_reason')
        .annotate(n=Count('pk'))
        .order_by('day')
    )

    by_day_status = {d: {} for d in date_strs}
    by_day_reason = {d: {} for d in date_strs}
    for row in attempt_qs:
        d = str(row['day'])
        if d not in by_day_status:
            continue
        s = row['status']
        by_day_status[d][s] = by_day_status[d].get(s, 0) + row['n']
        if row['failure_reason']:
            r = row['failure_reason']
            by_day_reason[d][r] = by_day_reason[d].get(r, 0) + row['n']

    chart_labels = [d[5:] for d in date_strs]  # MM-DD

    # Chart 1 — stacked bar: attempt volume by status
    # Shows all attempts (including retries), not final job states.
    status_series = [
        {'name': 'Done',   'status': 'done',   'color': '#16a34a'},
        {'name': 'Failed', 'status': 'failed', 'color': '#dc2626'},
    ]
    volume_datasets = [
        {
            'label': s['name'],
            'data':  [by_day_status[d].get(s['status'], 0) for d in date_strs],
            'color': s['color'],
        }
        for s in status_series
    ]

    # Chart 2 — failure rate % (line) + total volume (bar, dual axis)
    # Now based on JobAttemptLog, so it counts ALL attempts even if later retried and fixed.
    daily_totals  = [sum(by_day_status[d].values()) for d in date_strs]
    daily_failed  = [by_day_status[d].get('failed', 0) for d in date_strs]
    failure_rates = [
        round(f / t * 100, 1) if t else 0
        for f, t in zip(daily_failed, daily_totals)
    ]

    # Chart 3 — failure reason breakdown (stacked area)
    all_reasons = sorted({r for d in date_strs for r in by_day_reason[d]})
    reason_datasets = [
        {
            'label': reason,
            'data':  [by_day_reason[d].get(reason, 0) for d in date_strs],
            'color': FAILURE_COLORS.get(reason, '#6b7280'),
        }
        for reason in all_reasons
    ]

    # ── Failed jobs grouped by reason + signature (bulk retry) ──────────────
    failed_groups = list(
        ScanJob.objects
        .filter(status=ScanJob.STATUS_FAILED)
        .values('failure_reason', 'failure_signature')
        .annotate(count=Count('pk'))
        .order_by('-count')
    )

    # ── Recent jobs table ───────────────────────────────────────────────────
    status_filter = request.GET.get('status', '')
    reason_filter = request.GET.get('reason', '')
    recent_qs = ScanJob.objects.select_related('user').order_by('-created_at')
    if status_filter:
        recent_qs = recent_qs.filter(status=status_filter)
    if reason_filter:
        recent_qs = recent_qs.filter(failure_reason=reason_filter)
    recent_jobs = recent_qs[:100]

    ctx = {
        'counts':                   counts,
        'month_cost':               month_cost,
        'month_input':              month_input,
        'month_output':             month_output,
        'chart_labels_json':        json.dumps(chart_labels),
        'volume_datasets_json':     json.dumps(volume_datasets),
        'failure_rates_json':       json.dumps(failure_rates),
        'daily_totals_json':        json.dumps(daily_totals),
        'reason_datasets_json':     json.dumps(reason_datasets),
        'monthly_cost_labels_json': json.dumps(monthly_cost_labels),
        'monthly_cost_values_json': json.dumps(monthly_cost_values),
        'failed_groups':            failed_groups,
        'recent_jobs':              recent_jobs,
        'status_filter':            status_filter,
        'reason_filter':            reason_filter,
        'status_choices':           ScanJob.STATUS_CHOICES,
        'failure_reason_choices':   ScanJob.FAILURE_REASON_CHOICES,
    }
    return render(request, 'staff/dashboard.html', ctx)


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

    count = _reenqueue_jobs(jobs)
    messages.success(request, f'Re-enqueued {count} job(s).')
    return redirect('staff_dashboard')


@staff_required
@require_POST
def staff_retry_single(request, pk):
    job   = get_object_or_404(ScanJob, pk=pk, status=ScanJob.STATUS_FAILED)
    count = _reenqueue_jobs([job])
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'count': count})
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
    count = _reenqueue_jobs(jobs)
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'count': count})
    messages.success(request, f'Re-enqueued {count} job(s).')
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
