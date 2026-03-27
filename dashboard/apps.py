# dashboard/apps.py
from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'

    def ready(self):
        # Import gcal to register the pre_delete signal on Event
        import dashboard.gcal  # noqa: F401
