# billing/tests/models/test_coupon.py
import uuid
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from django.db import IntegrityError
from django.test import TestCase as DjangoTestCase

from billing.models import Coupon
from billing.tests.helpers import make_user


def _unique_code():
    return f'TST{uuid.uuid4().hex[:5].upper()}'


class TestCouponStripeInteraction(DjangoTestCase):
    """Unit tests — Stripe patched out."""

    def _make_coupon(self, code=None, percent='10.00', max_redemptions=None, head=None):
        """Create a Coupon with _push_to_stripe stubbed."""
        with patch.object(Coupon, '_push_to_stripe'):
            return Coupon.objects.create(
                code=code or _unique_code(),
                percent=percent,
                max_redemptions=max_redemptions,
                head=head,
            )

    def test_stripe_push_on_create(self):
        """Saving a new Coupon calls stripe.Coupon.create and stripe.PromotionCode.create."""
        code = _unique_code()
        fake_coupon = MagicMock(id=f'nvd-{code.lower()}')
        fake_promo = MagicMock(id='promo_abc')

        with patch('billing.models.stripe.Coupon.create', return_value=fake_coupon) as mc, \
             patch('billing.models.stripe.PromotionCode.create', return_value=fake_promo) as mp:
            coupon = Coupon.objects.create(code=code, percent='15.00')

        mc.assert_called_once()
        mp.assert_called_once()
        # IDs stored on the row
        coupon.refresh_from_db()
        self.assertEqual(coupon.stripe_coupon_id, fake_coupon.id)
        self.assertEqual(coupon.stripe_promotion_code_id, fake_promo.id)

    def test_stripe_push_not_called_on_update(self):
        """Saving an existing Coupon does NOT re-call Stripe."""
        coupon = self._make_coupon()
        with patch('billing.models.stripe.Coupon.create') as mc, \
             patch('billing.models.stripe.PromotionCode.create') as mp:
            coupon.save()  # update — pk already set
        mc.assert_not_called()
        mp.assert_not_called()

    def test_code_unique(self):
        """Duplicate code raises IntegrityError."""
        code = _unique_code()
        self._make_coupon(code=code)
        with self.assertRaises(IntegrityError):
            self._make_coupon(code=code)

    def test_unlimited_when_max_redemptions_null(self):
        """PromotionCode created without max_redemptions kwarg when field is null."""
        code = _unique_code()
        fake_coupon = MagicMock(id=f'nvd-{code.lower()}')
        fake_promo = MagicMock(id='promo_xyz')

        with patch('billing.models.stripe.Coupon.create', return_value=fake_coupon), \
             patch('billing.models.stripe.PromotionCode.create', return_value=fake_promo) as mp:
            Coupon.objects.create(code=code, percent='10.00', max_redemptions=None)

        _, kwargs = mp.call_args
        self.assertNotIn('max_redemptions', kwargs)

    def test_max_redemptions_passed_to_stripe(self):
        """When max_redemptions is set, value is forwarded to PromotionCode.create."""
        code = _unique_code()
        fake_coupon = MagicMock(id=f'nvd-{code.lower()}')
        fake_promo = MagicMock(id='promo_xyz')

        with patch('billing.models.stripe.Coupon.create', return_value=fake_coupon), \
             patch('billing.models.stripe.PromotionCode.create', return_value=fake_promo) as mp:
            Coupon.objects.create(code=code, percent='10.00', max_redemptions=5)

        _, kwargs = mp.call_args
        self.assertEqual(kwargs['max_redemptions'], 5)

    def test_head_null_allowed(self):
        """Coupon with head=None saves fine."""
        coupon = self._make_coupon(head=None)
        self.assertIsNone(coupon.head)
