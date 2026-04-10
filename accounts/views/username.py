# accounts/views/username.py
from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.models import User


def username_pick(request):
    if not request.user.is_authenticated:
        return redirect('accounts:login')

    if request.user.username and request.user.username != request.user.email.split('@')[0]:
        return redirect('dashboard:index')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()

        if not username:
            messages.error(request, 'Username cannot be empty.')
            return render(request, 'accounts/username_pick.html')

        if not username.replace('_', '').isalnum():
            messages.error(request, 'Only letters, numbers, and underscores allowed.')
            return render(request, 'accounts/username_pick.html')

        from emails.webhook import RESERVED_USERNAMES
        if username in RESERVED_USERNAMES:
            messages.error(request, 'That username is reserved. Please choose another.')
            return render(request, 'accounts/username_pick.html')

        if User.objects.filter(username=username).exclude(pk=request.user.pk).exists():
            messages.error(request, 'That username is already taken.')
            return render(request, 'accounts/username_pick.html')

        request.user.username = username
        request.user.save(update_fields=['username'])
        return redirect('billing:membership')

    return render(request, 'accounts/username_pick.html')
