# project/staff/dashboard.py
import datetime
import json
from functools import wraps

from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.models import MonthlyUsage, User
from emails.models import ScanJob

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


@staff_required
def staff_dashboard(request):
    now   = timezone.now()
    today = now.date()

    counts = ScanJob.objects.aggregate(
        total_today  = Count('pk', filter=Q(created_at__date=today)),
        done_today   = Count('pk', filter=Q(status=ScanJob.STATUS_DONE, updated_at__date=today)),
        failed       = Count('pk', filter=Q(status=ScanJob.STATUS_FAILED)),
        needs_review = Count('pk', filter=Q(status=ScanJob.STATUS_NEEDS_REVIEW)),
        in_flight    = Count('pk', filter=Q(status__in=[ScanJob.STATUS_QUEUED, ScanJob.STATUS_PROCESSING])),
        total_all    = Count('pk'),
    )

    snap = MonthlyUsage.objects.filter(year=today.year, month=today.month).aggregate(
        inp=Sum('input_tokens'), out=Sum('output_tokens')
    )
    roll = User.objects.aggregate(inp=Sum('monthly_input_tokens'), out=Sum('monthly_output_tokens'))
    month_input  = (snap['inp'] or 0) + (roll['inp'] or 0)
    month_output = (snap['out'] or 0) + (roll['out'] or 0)
    month_cost   = _cost(month_input, month_output)

    hist = list(
        MonthlyUsage.objects
        .values('year', 'month')
        .annotate(inp=Sum('input_tokens'), out=Sum('output_tokens'))
        .order_by('-year', '-month')[:6]
    )
    hist.reverse()
    monthly_cost_labels = [f"{r['year']}-{r['month']:02d}" for r in hist]
    monthly_cost_values = [_cost(r['inp'] or 0, r['out'] or 0) for r in hist]

    cutoff     = now - datetime.timedelta(days=29)
    date_range = _date_range(30)
    date_strs  = [d.isoformat() for d in date_range]

    job_qs = (
        ScanJob.objects
        .filter(updated_at__gte=cutoff)
        .extra(select={'day': 'DATE(updated_at)'})
        .values('day', 'status', 'failure_reason')
        .annotate(n=Count('pk'))
        .order_by('day')
    )

    by_day_status = {d: {} for d in date_strs}
    by_day_reason = {d: {} for d in date_strs}
    for row in job_qs:
        d = str(row['day'])
        if d not in by_day_status:
            continue
        s = row['status']
        by_day_status[d][s] = by_day_status[d].get(s, 0) + row['n']
        if row['failure_reason'] and s == ScanJob.STATUS_FAILED:
            r = row['failure_reason']
            by_day_reason[d][r] = by_day_reason[d].get(r, 0) + row['n']

    chart_labels = [d[5:] for d in date_strs]

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

    daily_totals  = [sum(by_day_status[d].values()) for d in date_strs]
    daily_failed  = [by_day_status[d].get('failed', 0) for d in date_strs]
    failure_rates = [
        round(f / t * 100, 1) if t else 0
        for f, t in zip(daily_failed, daily_totals)
    ]

    all_reasons = sorted({r for d in date_strs for r in by_day_reason[d]})
    reason_datasets = [
        {
            'label': reason,
            'data':  [by_day_reason[d].get(reason, 0) for d in date_strs],
            'color': FAILURE_COLORS.get(reason, '#6b7280'),
        }
        for reason in all_reasons
    ]

    failed_groups = list(
        ScanJob.objects
        .filter(status=ScanJob.STATUS_FAILED)
        .values('failure_reason')
        .annotate(count=Count('pk'))
        .order_by('-count')
    )

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
