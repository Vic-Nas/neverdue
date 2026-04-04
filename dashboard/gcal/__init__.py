# dashboard/gcal/__init__.py
from .crud import delete_from_gcal, patch_event_color, patch_event, update_event
from .watch import stop_gcal_watch, register_gcal_watch
from .signals import event_pre_delete
