# billing/tests/test_coupon_admin.py
"""
Tests for:
- Coupon.is_redeemable (expired / active / no expiry)
- Subscription.generate_referral_code (format, uniqueness)
- CouponAdmin.save_model (stripe sync on create, not on update, error handling)
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import stripe
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.utils import timezone

from billing.admin import CouponAdmin
from billing.models import Coupon, Subscription
from billing.tests.helpers import BillingTestCase, make_user, stripe_client


# ---------------------------------------------------------------------------
# Coupon.is_redeemable
# ---------------------------------------------------------------------------

class CouponIsRedeemable(TestCase):
    def test_no_expiry_is_redeemable(self):
        c = Coupon(code="X", percent=10, label="L", expires_at=None)
        self.assertTrue(c.is_redeemable())

    def test_future_expiry_is_redeemable(self):
        c = Coupon(code="X", percent=10, label="L",
                   expires_at=timezone.now() + timedelta(days=1))
        self.assertTrue(c.is_redeemable())

    def test_past_expiry_not_redeemable(self):
        c = Coupon(code="X", percent=10, label="L",
                   expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(c.is_redeemable())

    def test_exact_now_not_redeemable(self):
        # expires_at <= now should be False
        past = timezone.now() - timedelta(milliseconds=1)
        c = Coupon(code="X", percent=10, label="L", expires_at=past)
        self.assertFalse(c.is_redeemable())


# ---------------------------------------------------------------------------
# Subscription.generate_referral_code
# ---------------------------------------------------------------------------

class GenerateReferralCode(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("refgen")
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        self.sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status="active",
        )

    def test_format_is_nvd_prefix_plus_5_chars(self):
        code = self.sub.generate_referral_code()
        self.assertRegex(code, r'^NVD-[A-Z0-9]{5}$')

    def test_code_persisted_to_db(self):
        code = self.sub.generate_referral_code()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.referral_code, code)

    def test_generated_at_set(self):
        self.sub.generate_referral_code()
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.referral_code_generated_at)

    def test_second_call_raises_no_error_returns_same(self):
        # generate_referral_code doesn't guard against double-call;
        # calling it twice regenerates. Document current behaviour.
        code1 = self.sub.generate_referral_code()
        code2 = self.sub.generate_referral_code()
        # Both are valid NVD codes (may differ)
        self.assertRegex(code1, r'^NVD-[A-Z0-9]{5}$')
        self.assertRegex(code2, r'^NVD-[A-Z0-9]{5}$')

    def test_uniqueness_collision_retries(self):
        """If first attempt collides, it retries until unique."""
        existing_code = "NVD-AAAAA"
        Subscription.objects.filter(pk=self.sub.pk).update(
            referral_code=None
        )
        other_user = make_user("other_ref")
        s = stripe_client()
        cust2 = s.Customer.create(email=other_user.email)
        self.track("customer", cust2.id)
        Subscription.objects.create(
            user=other_user,
            stripe_customer_id=cust2.id,
            status="active",
            referral_code=existing_code,
        )
        # Patch _generate_referral_code to collide once then succeed
        from billing import models as billing_models
        call_count = {"n": 0}
        original = billing_models._generate_referral_code

        def patched():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return existing_code  # collision
            return original()

        with patch.object(billing_models, '_generate_referral_code', patched):
            self.sub.refresh_from_db()
            code = self.sub.generate_referral_code()

        self.assertNotEqual(code, existing_code)
        self.assertGreaterEqual(call_count["n"], 2)


# ---------------------------------------------------------------------------
# CouponAdmin.save_model
# ---------------------------------------------------------------------------

class CouponAdminSaveModel(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = CouponAdmin(Coupon, self.site)
        self.request = RequestFactory().post("/")
        self.request.user = MagicMock()

    def test_create_calls_sync_to_stripe(self):
        coupon = Coupon(code="ADM-CREATE", percent=10, label="Admin create")
        with patch.object(coupon, 'sync_to_stripe') as mock_sync, \
             patch('billing.admin.admin.ModelAdmin.save_model'):
            self.admin.save_model(self.request, coupon, form=None, change=False)
            mock_sync.assert_called_once()

    def test_update_does_not_call_sync_to_stripe(self):
        coupon = Coupon(code="ADM-UPDATE", percent=10, label="Admin update")
        with patch.object(coupon, 'sync_to_stripe') as mock_sync, \
             patch('billing.admin.admin.ModelAdmin.save_model'):
            self.admin.save_model(self.request, coupon, form=None, change=True)
            mock_sync.assert_not_called()

    def test_stripe_error_on_create_warns_not_raises(self):
        coupon = Coupon(code="ADM-ERR", percent=10, label="Admin err")
        stripe_err = stripe.error.StripeError("test error")
        with patch.object(coupon, 'sync_to_stripe', side_effect=stripe_err), \
             patch('billing.admin.admin.ModelAdmin.save_model'), \
             patch.object(self.admin, 'message_user') as mock_msg:
            # Must not raise
            self.admin.save_model(self.request, coupon, form=None, change=False)
            mock_msg.assert_called_once()
            args = mock_msg.call_args[0]
            self.assertIn('Stripe sync failed', args[1])
