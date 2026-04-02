import pytest
from django.test import RequestFactory
from accounts.context_processors import global_settings


@pytest.mark.django_db
class TestContextProcessor:
    def test_returns_domain(self, settings):
        settings.DOMAIN = 'example.com'
        request = RequestFactory().get('/')
        ctx = global_settings(request)
        assert ctx['DOMAIN'] == 'example.com'

    def test_adsense_slots_padded(self, settings):
        settings.ADSENSE_SLOTS = ['a']
        request = RequestFactory().get('/')
        ctx = global_settings(request)
        assert len(ctx['ADSENSE_SLOTS']) == 3
        assert ctx['ADSENSE_SLOTS'][0] == 'a'

    def test_adsense_client_id_missing(self, settings):
        if hasattr(settings, 'ADSENSE_CLIENT_ID'):
            delattr(settings, 'ADSENSE_CLIENT_ID')
        request = RequestFactory().get('/')
        ctx = global_settings(request)
        assert ctx['ADSENSE_CLIENT_ID'] is None
