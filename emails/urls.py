# emails/urls.py
from django.urls import path
from . import views

app_name = 'emails'

urlpatterns = [
    path('inbound/', views.inbound, name='inbound'),
]