from django.urls import path
from . import views
from . import webhook

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    # Literal paths first (more specific)
    path('events/new/', views.event_edit, name='event_create'),
    path('events/bulk/', views.events_bulk_action, name='events_bulk_action'),
    path('events/export/', views.export_events, name='events_export'),
    # Parameterized paths (less specific)
    path('events/<int:pk>/', views.event_detail, name='event_detail'),
    path('events/<int:pk>/edit/', views.event_edit, name='event_edit'),
    path('events/<int:pk>/delete/', views.event_delete, name='event_delete'),
    path('events/<int:pk>/prompt-edit/', views.event_prompt_edit, name='event_prompt_edit'),
    path('categories/', views.categories, name='categories'),
    path('categories/new/', views.category_edit, name='category_create'),
    path('categories/<int:pk>/', views.category_detail, name='category_detail'),
    path('categories/<int:pk>/edit/', views.category_edit, name='category_edit'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),
    path('queue/',        views.queue,        name='queue'),
    path('queue/status/', views.queue_status, name='queue_status'),
    path('queue/<int:pk>/', views.queue_job_detail, name='queue_job_detail'),
    path('queue/<int:pk>/reprocess/', views.queue_job_reprocess, name='queue_job_reprocess'),
    path('queue/<int:pk>/retry/',     views.queue_job_retry,     name='queue_job_retry'),
    path('sources/', views.email_sources, name='email_sources'),
    path('sources/add/', views.filter_rule_add, name='filter_rule_add'),
    path('sources/<int:pk>/delete/', views.filter_rule_delete, name='filter_rule_delete'),
    path('rules/', views.rules, name='rules'),
    path('rules/add/', views.rule_add, name='rule_add'),
    path('rules/<int:pk>/delete/', views.rule_delete, name='rule_delete'),
    path('upload/', views.upload, name='upload'),
    path('gcal/webhook/', webhook.gcal_webhook, name='gcal_webhook'),
]
