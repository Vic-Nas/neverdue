# project/urls.py
from django.contrib import admin
from django.urls import path, include
import project.views as views
import project.staff as staff_views

urlpatterns = [
    # Staff dashboard + actions
    path('staff/',                    staff_views.staff_dashboard,    name='staff_dashboard'),
    path('staff/retry/',              staff_views.staff_retry_jobs,   name='staff_retry_jobs'),
    path('staff/retry/<int:pk>/',     staff_views.staff_retry_single, name='staff_retry_single'),
    path('staff/delete/<int:pk>/',    staff_views.staff_delete_single, name='staff_delete_single'),
    path('staff/bulk-retry/',         staff_views.staff_bulk_retry,   name='staff_bulk_retry'),
    path('staff/bulk-delete/',        staff_views.staff_bulk_delete,  name='staff_bulk_delete'),

    # Django admin — limited models only, staff-only URL
    path('staff/admin/',              admin.site.urls),

    path('',           include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('billing/',   include('billing.urls')),
    path('emails/',    include('emails.urls')),
    path('legal/',     include('project.legal_urls')),
    path('help/',      views.help_page, name='help'),
    path('support/',   include('support.urls')),

    # dj-stripe webhook — replaces /billing/webhook/
    path('stripe/', include('djstripe.urls', namespace='djstripe')),
]
