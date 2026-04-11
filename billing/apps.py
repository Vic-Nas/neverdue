# billing/apps.py
from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = 'billing'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import billing.signals  # noqa: F401 — registers signal handlers
