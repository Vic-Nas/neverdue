# project/celery.py
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')

app = Celery('project')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Explicitly import tasks to ensure they're registered with Celery
from emails import tasks as _  # noqa

# Celery Beat schedule
app.conf.beat_schedule = {
    'cleanup-events-daily': {
        'task': 'emails.tasks.cleanup_events',
        'schedule': crontab(hour=3, minute=0),  # runs at 3am UTC daily
    },
    'recover-stale-jobs': {
        'task': 'emails.tasks.recover_stale_jobs',
        'schedule': crontab(minute='*/10'),  # runs every 10 minutes
    },
}
