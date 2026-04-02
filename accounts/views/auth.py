import logging

from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


def login(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    return render(request, 'accounts/login.html')


def logout(request):
    if request.user.is_authenticated and request.user.revoke_google_on_logout:
        from accounts.utils import revoke_google_token
        revoke_google_token(request.user)
    auth_logout(request)
    return redirect('accounts:login')
