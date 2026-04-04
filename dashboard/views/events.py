# dashboard/views/events.py
import json as _json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.utils.dateparse import parse_date

from dashboard.models import Category, Event

logger = logging.getLogger(__name__)


@login_required
def index(request):
    try:
        qs = Event.objects.filter(
            user=request.user, status='active',
        ).select_related('category')

        # Search
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(title__icontains=q)

        # Sort
        sort = request.GET.get('sort', 'start')
        if sort == 'added':
            qs = qs.order_by('-created_at')
        else:
            sort = 'start'
            qs = qs.order_by('start')

        # Pagination
        from django.core.paginator import Paginator
        page_num = request.GET.get('page', '1')
        paginator = Paginator(qs, 25)
        page = paginator.get_page(page_num)

        return render(request, 'dashboard/index.html', {
            'active_events': page.object_list,
            'page_obj': page,
            'sort': sort,
            'q': q,
            'total_count': paginator.count,
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
        event = get_object_or_404(Event, pk=pk, user=request.user) if pk else None
        categories = Category.objects.filter(user=request.user).order_by('name')

        if request.method == 'POST':
            data = _json.loads(request.body)
            title = data.get('title', '').strip()
            start_str = data.get('start', '')
            end_str = data.get('end', '')
            description = data.get('description', '').strip()
            category_id = data.get('category_id')
            color = data.get('color', '').strip()
            recurrence_freq = data.get('recurrence_freq') or None
            recurrence_until_str = data.get('recurrence_until') or ''
            raw_reminders = data.get('reminders', [])
            reminders = [int(m) for m in raw_reminders if isinstance(m, (int, float)) and int(m) > 0]

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
            event.recurrence_freq = recurrence_freq
            event.recurrence_until = parse_date(recurrence_until_str) if recurrence_until_str else None
            event.reminders = reminders

            if was_pending:
                event.status = 'active'
                event.pending_expires_at = None

            event.save()

            # Push changes to GCal asynchronously
            if event.google_event_id and request.user.save_to_gcal:
                from dashboard.tasks import sync_event_to_gcal
                sync_event_to_gcal.defer(event_id=event.pk)

            return JsonResponse({'ok': True, 'pk': event.pk})

        return render(request, 'dashboard/event_edit.html', {'event': event, 'categories': categories})
    except Exception:
        logger.exception("event_edit error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not save event.', status=500)


@login_required
def event_delete(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        if request.method == 'POST':
            event.delete()
            return redirect('dashboard:index')
        return render(request, 'dashboard/event_delete.html', {'event': event})
    except Exception:
        logger.exception("event_delete error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not delete event.', status=500)
