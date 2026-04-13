# dashboard/views/categories.py
import json as _json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from dashboard.models import Category, Event
from accounts.views import GCAL_COLOR_HEX

# Maps gcal_color_id (str) → hex used for in-app UI coloring
_GCAL_HEX = {
    '1':  '#7986CB',  # Lavender
    '2':  '#33B679',  # Sage
    '3':  '#8E24AA',  # Grape
    '4':  '#E67C73',  # Flamingo
    '5':  '#F6BF26',  # Banana
    '6':  '#F4511E',  # Tangerine
    '7':  '#039BE5',  # Peacock
    '8':  '#3F51B5',  # Blueberry
    '9':  '#0B8043',  # Basil
    '10': '#D50000',  # Tomato
    '11': '#616161',  # Graphite
}

# Priority fallback colors (matches CSS tokens in categories.css)
_PRIORITY_HEX = {
    1: '#6366f1',  # Low    → indigo (accent)
    2: '#f59e0b',  # Medium → amber
    3: '#ef4444',  # High   → red
    4: '#dc2626',  # Urgent → deep red
}


def _resolve_color(gcal_color_id, priority):
    """Return the hex color to store on the category for UI use."""
    if gcal_color_id and gcal_color_id in _GCAL_HEX:
        return _GCAL_HEX[gcal_color_id]
    return _PRIORITY_HEX.get(priority, '#6366f1')

logger = logging.getLogger(__name__)


@login_required
def categories(request):
    try:
        cats = Category.objects.filter(user=request.user)

        q = request.GET.get('q', '').strip()
        if q:
            cats = cats.filter(name__icontains=q)

        sort = request.GET.get('sort', 'name')
        if sort == 'priority':
            cats = cats.order_by('-priority', 'name')
        else:
            sort = 'name'
            cats = cats.order_by('name')

        from django.core.paginator import Paginator
        paginator = Paginator(cats, 10)
        page = paginator.get_page(request.GET.get('page', '1'))

        return render(request, 'dashboard/categories.html', {
            'categories': page.object_list, 'page_obj': page,
            'q': q, 'sort': sort, 'total_count': paginator.count,
            'gcal_hex': _GCAL_HEX,
        })
    except Exception:
        logger.exception("categories error for user=%s", request.user.pk)
        return HttpResponse('Categories unavailable.', status=500)


@login_required
def category_detail(request, pk):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user)
        events = Event.objects.filter(category=category, user=request.user, status='active').order_by('start')
        # Resolve display color: stored hex → gcal swatch hex → None (template uses priority CSS class)
        gcal_color_hex = _GCAL_HEX.get(category.gcal_color_id, '') if category.gcal_color_id else ''
        return render(request, 'dashboard/category_detail.html', {
            'category': category, 'events': events, 'gcal_color_hex': gcal_color_hex,
        })
    except Exception:
        logger.exception("category_detail error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Category unavailable.', status=500)


@login_required
def category_edit(request, pk=None):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user) if pk else None

        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            priority = int(request.POST.get('priority', 2)) or 2
            gcal_color_id = request.POST.get('gcal_color_id', '').strip() or None
            reminders = request.POST.getlist('reminders')
            reminders = [int(r) for r in reminders if r.isdigit()]

            if not name:
                from django.http import JsonResponse
                return JsonResponse({'ok': False, 'error': 'Name is required.'}, status=400)

            if category is None:
                category = Category(user=request.user)

            old_color = category.gcal_color_id
            old_reminders = list(category.reminders)
            category.name = name
            category.priority = priority
            category.gcal_color_id = gcal_color_id
            category.reminders = reminders
            category.color = _resolve_color(gcal_color_id, priority)
            category.save()

            if gcal_color_id != old_color:
                from dashboard.tasks import patch_category_colors
                patch_category_colors.defer(user_id=request.user.pk, category_id=category.pk)

            if reminders != old_reminders:
                from dashboard.tasks import patch_category_reminders
                patch_category_reminders.defer(user_id=request.user.pk, category_id=category.pk)

            return redirect('dashboard:categories')

        return render(request, 'dashboard/category_edit.html', {
            'category': category, 'gcal_color_hex': GCAL_COLOR_HEX,
        })
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
        return render(request, 'dashboard/category_delete.html', {
            'category': category, 'categories_url': '/dashboard/categories/',
        })
    except Exception:
        logger.exception("category_delete error for user=%s pk=%s", request.user.pk, pk)
        return HttpResponse('Could not delete category.', status=500)


@login_required
def categories_bulk_delete(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        ids = [int(i) for i in data.get('ids', [])]
        count, _ = Category.objects.filter(pk__in=ids, user=request.user).delete()
        return JsonResponse({'ok': True, 'deleted': count})
    except Exception:
        logger.exception("categories_bulk_delete error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)
