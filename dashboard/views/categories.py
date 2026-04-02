import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from dashboard.models import Category, Event
from accounts.views import GCAL_COLOR_HEX

logger = logging.getLogger(__name__)


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
            'category': category, 'events': events, 'gcal_color_hex': GCAL_COLOR_HEX,
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
            category.name = name
            category.priority = priority
            category.gcal_color_id = gcal_color_id
            category.reminders = reminders
            category.save()

            if gcal_color_id != old_color:
                from dashboard.tasks import patch_category_colors
                patch_category_colors.defer(user_id=request.user.pk, category_id=category.pk)

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
