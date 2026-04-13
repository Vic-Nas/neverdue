# billing/tests/models/test_coupon.py
import uuid

from django.db import IntegrityError
from django.test import TestCase

from billing.models import Coupon
from billing.tests.helpers import make_user


def _unique_code():
    return f'TST{uuid.uuid4().hex[:5].upper()}'


def _make_coupon(code=None, percent='10.00', max_redemptions=None, head=None):
    return Coupon.objects.create(
        code=code or _unique_code(),
        percent=percent,
        max_redemptions=max_redemptions,
        head=head,
    )


class TestCouponModel(TestCase):
    """Unit tests — Stripe is not involved at coupon creation."""

    def test_code_unique(self):
        """Duplicate code raises IntegrityError."""
        code = _unique_code()
        _make_coupon(code=code)
        with self.assertRaises(IntegrityError):
            _make_coupon(code=code)

    def test_head_null_allowed(self):
        """Coupon with head=None saves fine."""
        coupon = _make_coupon(head=None)
        self.assertIsNone(coupon.head)

    def test_max_redemptions_nullable(self):
        """max_redemptions can be null (unlimited)."""
        coupon = _make_coupon(max_redemptions=None)
        self.assertIsNone(coupon.max_redemptions)

    def test_max_redemptions_set(self):
        """max_redemptions is stored correctly when provided."""
        coupon = _make_coupon(max_redemptions=5)
        self.assertEqual(coupon.max_redemptions, 5)

    def test_percent_stored(self):
        """percent is stored as given."""
        coupon = _make_coupon(percent='15.00')
        coupon.refresh_from_db()
        self.assertEqual(float(coupon.percent), 15.0)

    def test_head_fk_stored(self):
        """head FK is stored correctly."""
        user = make_user('head_fk_user')
        coupon = _make_coupon(head=user)
        coupon.refresh_from_db()
        self.assertEqual(coupon.head, user)
