# dashboard/gcal/signals.py
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .crud import delete_from_gcal


@receiver(pre_delete, sender='dashboard.Event')
def event_pre_delete(sender, instance, **kwargs):
    if getattr(instance, '_skip_gcal_delete', False):
        return
    if instance.status == 'pending':
        return
    if not instance.google_event_id:
        return
    delete_from_gcal(instance.user, instance.google_event_id)
