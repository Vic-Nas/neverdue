# dashboard/views/events.py
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_datetime

from dashboard.models import Category, Event

logger = logging.getLogger(__name__)


@login_required
def index(request):
    try:
        qs = Event.objects.filter(
            user=request.user, status='active',
        ).select_related('category')

        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(title__icontains=q)

        sort = request.GET.get('sort', 'start')
        if sort == 'added':
            qs = qs.order_by('-created_at')
        else:
            sort = 'start'
            qs = qs.order_by('start')

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


def _parse_links(post):
    """Build [{url, title}] from parallel link_urls / link_titles form lists."""
    urls = post.getlist('link_urls')
    titles = post.getlist('link_titles')
    links = []
    for url, title in zip(urls, titles):
        url = url.strip()
        if url:
            links.append({'url': url, 'title': title.strip()})
    return links


@login_required
def event_edit(request, pk=None):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user) if pk else None
        categories = Category.objects.filter(user=request.user).order_by('name')

        if request.method == 'POST':
            post = request.POST
            title = post.get('title', '').strip()
            start_str = post.get('start', '')
            end_str = post.get('end', '')
            description = post.get('description', '').strip()
            category_id = post.get('category') or None
            color = post.get('color', '').strip()
            recurrence_freq = post.get('recurrence_freq') or None
            recurrence_until_str = post.get('recurrence_until', '').strip()
            raw_reminders = post.getlist('reminders')
            reminders = [int(m) for m in raw_reminders if m.strip().isdigit() and int(m) > 0]
            links = _parse_links(post)

            if not title or not start_str or not end_str:
                return render(request, 'dashboard/event_edit.html', {
                    'event': event, 'categories': categories,
                    'error': 'Title, start, and end are required.',
                })

            start = parse_datetime(start_str)
            end = parse_datetime(end_str)
            if not start or not end:
                return render(request, 'dashboard/event_edit.html', {
                    'event': event, 'categories': categories,
                    'error': 'Invalid date format.',
                })

            category = None
            if category_id:
                try:
                    category = Category.objects.get(pk=category_id, user=request.user)
                except Category.DoesNotExist:
                    return render(request, 'dashboard/event_edit.html', {
                        'event': event, 'categories': categories,
                        'error': 'Category not found.',
                    })

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
            event.links = links

            if was_pending:
                event.status = 'active'
                event.pending_expires_at = None

            event.save()

            if event.google_event_id and request.user.save_to_gcal:
                from dashboard.tasks import sync_event_to_gcal
                sync_event_to_gcal.defer(event_id=event.pk)

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
            event.delete()
            return redirect('dashboard:index')
        return render(request, 'dashboard/event_delete.html', {'event': event})
    except Exception:
        logger.exception("event_delete error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not delete event.', status=500)