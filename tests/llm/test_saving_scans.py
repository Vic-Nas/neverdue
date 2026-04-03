import pytest
from unittest.mock import MagicMock
from llm.pipeline.outcome import ProcessingOutcome
from llm.pipeline.saving import _check_and_increment_scans, _get_or_create_uncategorized


class TestProcessingOutcome:
    def test_defaults(self):
        o = ProcessingOutcome()
        assert o.status == 'done'
        assert o.created == []
        assert o.failure_reason == ''


@pytest.mark.django_db
class TestCheckAndIncrementScans:
    def test_free_user_increments(self, user):
        from django.utils import timezone as tz
        user.monthly_scans = 0
        user.scan_reset_date = tz.now().date()
        user.save()
        assert _check_and_increment_scans(user) is True

    def test_free_user_at_limit(self, user):
        from django.utils import timezone as tz
        user.monthly_scans = 30
        user.scan_reset_date = tz.now().date()
        user.save()
        assert _check_and_increment_scans(user) is False

    def test_pro_user_always_passes(self, pro_user):
        from django.utils import timezone as tz
        pro_user.monthly_scans = 999
        pro_user.scan_reset_date = tz.now().date()
        pro_user.save()
        assert _check_and_increment_scans(pro_user) is True


@pytest.mark.django_db
class TestGetOrCreateUncategorized:
    def test_creates(self, user):
        cat = _get_or_create_uncategorized(user)
        assert cat.name == 'Uncategorized'

    def test_idempotent(self, user):
        c1 = _get_or_create_uncategorized(user)
        c2 = _get_or_create_uncategorized(user)
        assert c1.pk == c2.pk
