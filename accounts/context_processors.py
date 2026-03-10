# accounts/context_processors.py
from django.conf import settings


def global_settings(request):
    return {
        'DOMAIN': settings.DOMAIN,
        'ADSENSE_CLIENT_ID': getattr(settings, 'ADSENSE_CLIENT_ID', None),
        'ADSENSE_SLOTS': getattr(settings, 'ADSENSE_SLOTS', []),
    }