# project/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
import project.views as views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('billing/', include('billing.urls')),
    path('emails/', include('emails.urls')),
    path('legal/', include('project.legal_urls')),
    path('help/', views.help_page, name='help'),
]