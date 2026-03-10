# project/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('accounts/', include('accounts.urls')),
    path('billing/', include('billing.urls')),
    path('emails/', include('emails.urls')),
    path('legal/', include('project.legal_urls')),
]