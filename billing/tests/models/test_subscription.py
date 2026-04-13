# billing/tests/models/test_subscription.py
import uuid
from unittest.mock import patch

from django.test import TestCase

from billing.models import Coupon, Subscription
from billing.tests.helpers import make_user


def _make_sub(username=None, status='active', customer_id=None):
    user = make_user(username or f'u_{uuid.uuid4().hex[:6]}')
    sub = Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or f'cus_{uuid.uuid4().hex[:10]}',
        status=status,
    )
    return sub


class TestGenerateReferralCode(TestCase):

    def _make_sub_with_stripe_stub(self):
        return _make_sub()

    def test_generate_referral_code_creates_coupon(self):
        """Creates a Coupon row with head=user, percent=12.5, max_redemptions=12."""
        sub = _make_sub()
        code = sub.generate_referral_code()
        self.assertTrue(code.startswith('NVD-'))
        coupon = Coupon.objects.get(code=code)
        self.assertEqual(coupon.head, sub.user)
        self.assertEqual(float(coupon.percent), 12.5)
        self.assertEqual(coupon.max_redemptions, 12)

    def test_generate_referral_code_idempotent(self):
        """Calling twice returns the same code; no second Coupon row created."""
        sub = _make_sub()
        code1 = sub.generate_referral_code()
            code2 = sub.generate_referral_code()
        self.assertEqual(code1, code2)
        self.assertEqual(Coupon.objects.filter(head=sub.user).count(), 1)

    def test_referral_code_property_none_before_generation(self):
        """referral_code returns None before generate_referral_code is called."""
        sub = _make_sub()
        self.assertIsNone(sub.referral_code)

    def test_referral_code_property_after_generation(self):
        """referral_code returns code string after generation."""
        sub = _make_sub()
        code = sub.generate_referral_code()
        sub.refresh_from_db()
        self.assertEqual(sub.referral_code, code)

    def test_generate_referral_code_unique_collision(self):
        """When random code collides, retries and succeeds."""
        sub = _make_sub()
        colliding_code = 'NVD-AAAAA'

        with patch('billing.models.random.choices') as mc:
            # First call returns a colliding code (pre-create it), second succeeds
            Coupon.objects.create(
                    code=colliding_code, percent='10.00',
                )
            mc.side_effect = [
                list('AAAAA'),   # collision
                list('BBBBB'),   # unique
            ]
            code = sub.generate_referral_code()

        self.assertEqual(code, 'NVD-BBBBB')

    def test_generate_referral_code_exhaustion(self):
        """After 10 collisions, raises RuntimeError."""
        sub = _make_sub()

        with patch('billing.models.Coupon.objects') as mock_qs:
            # filter().exists() always returns True → always collides
            mock_qs.filter.return_value.exists.return_value = True
            with self.assertRaises(RuntimeError):
                sub.generate_referral_code()
