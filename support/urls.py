# support/urls.py
from django.urls import path
from support import views

app_name = "support"

urlpatterns = [
    path("",                    views.submit,          name="submit"),
    path("tickets/",            views.my_tickets,      name="my_tickets"),
    path("<uuid:pk>/",          views.ticket_detail,   name="detail"),
    path("<uuid:pk>/resolve/",  views.resolve,         name="resolve"),
    path("gh-webhook/",         views.github_webhook,  name="gh_webhook"),
]