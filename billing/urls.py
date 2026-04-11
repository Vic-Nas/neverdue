# billing/urls.py
from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('membership/',             views.plans,                  name='membership'),
    path('checkout/',               views.checkout,               name='checkout'),
    path('success/',                views.success,                name='success'),
    path('cancel/',                 views.cancel,                 name='cancel'),
    path('portal/',                 views.portal,                 name='portal'),
    path('referral-code/generate/', views.generate_referral_code, name='generate_referral_code'),
]
