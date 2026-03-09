# dashboard/views.py
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Category, Event, Rule


@login_required
def index(request):
    try:
        events = Event.objects.filter(user=request.user).order_by('start')
        return render(request, 'dashboard/index.html', {'events': events})
    except Exception:
        return HttpResponse('Dashboard unavailable.', status=500)


@login_required
def event_detail(request, pk):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user)
        return render(request, 'dashboard/event_detail.html', {'event': event})
    except Exception:
        return HttpResponse('Event unavailable.', status=500)


@login_required
def event_edit(request, pk=None):
    try:
        event = get_object_or_404(Event, pk=pk, user=request.user) if pk else None
        categories = Category.objects.filter(user=request.user)
        if request.method == 'POST':
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            start = request.POST.get('start')
            end = request.POST.get('end')
            category_id = request.POST.get('category')
            category = get_object_or_404(Category, pk=category_id, user=request.user) if category_id else None
            if event:
                event.title = title
                event.description = description
                event.start = start
                event.end = end
                event.category = category
                event.save()
            else:
                event = Event.objects.create(
                    user=request.user,
                    title=title,
                    description=description,
                    start=start,
                    end=end,
                    category=category,
                )
            return redirect('dashboard:event_detail', pk=event.pk)
        return render(request, 'dashboard/event_edit.html', {'event': event, 'categories': categories})
    except Exception:
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
        return HttpResponse('Could not delete event.', status=500)


@login_required
def categories(request):
    try:
        cats = Category.objects.filter(user=request.user).prefetch_related('rules')
        return render(request, 'dashboard/categories.html', {'categories': cats})
    except Exception:
        return HttpResponse('Categories unavailable.', status=500)


@login_required
def category_edit(request, pk=None):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user) if pk else None
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            color = request.POST.get('color', '').strip()
            reminders_raw = request.POST.getlist('reminders')
            reminders = [{'minutes': int(m)} for m in reminders_raw if m.isdigit()]
            if category:
                category.name = name
                category.color = color
                category.reminders = reminders
                category.save()
            else:
                category = Category.objects.create(
                    user=request.user,
                    name=name,
                    color=color,
                    reminders=reminders,
                )
            # Handle rules
            category.rules.all().delete()
            senders = request.POST.getlist('rule_sender')
            keywords = request.POST.getlist('rule_keyword')
            for sender, keyword in zip(senders, keywords):
                if sender or keyword:
                    Rule.objects.create(
                        user=request.user,
                        category=category,
                        sender=sender or None,
                        keyword=keyword or None,
                    )
            return redirect('dashboard:categories')
        return render(request, 'dashboard/category_edit.html', {'category': category})
    except Exception:
        return HttpResponse('Could not save category.', status=500)


@login_required
def category_delete(request, pk):
    try:
        category = get_object_or_404(Category, pk=pk, user=request.user)
        if request.method == 'POST':
            category.delete()
            return redirect('dashboard:categories')
        return render(request, 'dashboard/category_delete.html', {'category': category})
    except Exception:
        return HttpResponse('Could not delete category.', status=500)


@login_required
def email_sources(request):
    try:
        return render(request, 'dashboard/email_sources.html')
    except Exception:
        return HttpResponse('Email sources unavailable.', status=500)


@login_required
def upload(request):
    try:
        if not request.user.is_pro:
            return redirect('billing:plans')
        categories = Category.objects.filter(user=request.user)
        return render(request, 'dashboard/upload.html', {'categories': categories})
    except Exception:
        return HttpResponse('Upload unavailable.', status=500)