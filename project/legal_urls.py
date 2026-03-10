# project/legal_urls.py
from django.urls import path
from . import views

app_name = 'legal'

urlpatterns = [
    path('privacy/', views.privacy, name='privacy'),
    path('terms/', views.terms, name='terms'),
]