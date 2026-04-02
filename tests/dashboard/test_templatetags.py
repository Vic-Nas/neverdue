import pytest
from datetime import datetime, timezone as dt_tz
from django.template import Template, Context
from dashboard.templatetags.tz_display import in_user_tz, user_tz_name


class TestInUserTz:
    def test_converts(self, user):
        dt = datetime(2026, 6, 1, 14, 0, tzinfo=dt_tz.utc)
        result = in_user_tz(dt, user)
        assert result.hour == 10  # America/Toronto = UTC-4 in June

    def test_none_passthrough(self, user):
        assert in_user_tz(None, user) is None

    def test_naive_treated_as_utc(self, user):
        dt = datetime(2026, 6, 1, 14, 0)
        result = in_user_tz(dt, user)
        assert result.hour == 10
