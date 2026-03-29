# project/urls.py
from django.urls import path, include
import project.views as views
import project.views_staff as staff_views

urlpatterns = [
    # Staff dashboard — replaces django.contrib.admin
    path('staff/',                    staff_views.staff_dashboard,    name='staff_dashboard'),
    path('staff/retry/',              staff_views.staff_retry_jobs,   name='staff_retry_jobs'),
    path('staff/retry/<int:pk>/',     staff_views.staff_retry_single, name='staff_retry_single'),

    path('',           include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('billing/',   include('billing.urls')),
    path('emails/',    include('emails.urls')),
    path('legal/',     include('project.legal_urls')),
    path('help/',      views.help_page, name='help'),
]
