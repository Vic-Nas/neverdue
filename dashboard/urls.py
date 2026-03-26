# dashboard/urls.py
from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('events/<int:pk>/', views.event_detail, name='event_detail'),
    path('events/new/', views.event_edit, name='event_create'),
    path('events/<int:pk>/edit/', views.event_edit, name='event_edit'),
    path('events/<int:pk>/delete/', views.event_delete, name='event_delete'),
    path('categories/', views.categories, name='categories'),
    path('categories/new/', views.category_edit, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_edit, name='category_edit'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),
    path('sources/', views.email_sources, name='email_sources'),
    path('sources/filters/add/', views.filter_rule_add, name='filter_rule_add'),
    path('sources/filters/<int:pk>/delete/', views.filter_rule_delete, name='filter_rule_delete'),
    path('upload/', views.upload, name='upload'),
]