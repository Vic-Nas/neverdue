# accounts/views/auth.py
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect, render


def login(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    return render(request, 'accounts/login.html')


def logout(request):
    auth_logout(request)
    return redirect('accounts:login')
