# billing/tests/tasks/helpers/test_prev_month_window.py
from datetime import datetime, timezone as dt_timezone

from django.test import TestCase

from billing.tasks import _prev_month_window


def _utc(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class PrevMonthWindowTest(TestCase):

    def test_march_1_gives_february(self):
        start, end = _prev_month_window(_utc(2024, 3, 1))
        self.assertEqual(start, _utc(2024, 2, 1))
        self.assertEqual(end, _utc(2024, 3, 1))

    def test_feb_1_gives_january(self):
        start, end = _prev_month_window(_utc(2024, 2, 1))
        self.assertEqual(start, _utc(2024, 1, 1))
        self.assertEqual(end, _utc(2024, 2, 1))

    def test_jan_1_gives_december_prior_year(self):
        start, end = _prev_month_window(_utc(2024, 1, 1))
        self.assertEqual(start, _utc(2023, 12, 1))
        self.assertEqual(end, _utc(2024, 1, 1))

    def test_both_datetimes_are_tz_aware(self):
        start, end = _prev_month_window(_utc(2024, 5, 1))
        self.assertIsNotNone(start.tzinfo)
        self.assertIsNotNone(end.tzinfo)
