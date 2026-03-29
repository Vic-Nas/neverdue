# accounts/context_processors.py
from django.conf import settings


def global_settings(request):
    slots = list(getattr(settings, "ADSENSE_SLOTS", []))
    while len(slots) < 3:
        slots.append("")
    return {
        "DOMAIN": settings.DOMAIN,
        "ADSENSE_CLIENT_ID": getattr(settings, "ADSENSE_CLIENT_ID", None),
        "ADSENSE_SLOTS": slots,
    }