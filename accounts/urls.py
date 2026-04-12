# accounts/urls.py
from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('',                          views.login,               name='login'),
    path('logout/',                   views.logout,              name='logout'),
    path('auth/google/',              views.google_login,        name='google_login'),
    path('auth/google/callback/',     views.google_callback,     name='google_callback'),
    path('preferences/',              views.preferences,         name='preferences'),
    path('preferences/revoke-google/', views.revoke_google,     name='revoke_google'),
    path('preferences/username/',     views.change_username,     name='change_username'),
    path('preferences/username/check/', views.check_username,   name='check_username'),
    path('tz/auto/',                  views.set_timezone_auto,   name='set_timezone_auto'),
    path('tz/set/',                   views.set_timezone_manual, name='set_timezone_manual'),
]