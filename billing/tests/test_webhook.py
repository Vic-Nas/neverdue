# billing/tests/test_webhook.py
"""
Webhook handler unit tests — uses real Stripe objects where needed
to verify the handler produces the right local state.
We call the internal functions directly (no HTTP) for speed;
signature verification is tested separately in test_webhook_http.py.
"""
import stripe
from django.utils import timezone

from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import BillingTestCase, make_user, stripe_client
from billing.views.webhook import (
    _handle_discount_created,
    _sync_subscription,
)


class SyncSubscriptionStatus(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("webhookuser")
        s = stripe_client()
        self.cust = s.Customer.create(email=self.user.email)
        self.track("customer", self.cust.id)
        self.sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status="cancelled",
        )

    def _fake_stripe_sub(self, status, sub_id="sub_fake123"):
        return {
            "id": sub_id,
            "customer": self.cust.id,
            "status": status,
            "items": {"data": [{"current_period_end": 9999999999}]},
        }

    def test_sync_active(self):
        _sync_subscription(self._fake_stripe_sub("active"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")
        self.assertTrue(self.sub.is_pro)

    def test_sync_trialing(self):
        _sync_subscription(self._fake_stripe_sub("trialing"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "trialing")

    def test_sync_cancelled(self):
        self.sub.status = "active"
        self.sub.save()
        _sync_subscription(self._fake_stripe_sub("canceled"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "canceled")

    def test_sync_unknown_customer_logs_warning(self):
        # Should not raise — just warn
        bad = self._fake_stripe_sub("active")
        bad["customer"] = "cus_doesnotexist"
        _sync_subscription(bad)  # must not raise

    def test_sync_stores_period_end(self):
        _sync_subscription(self._fake_stripe_sub("active"))
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.current_period_end)


class DiscountCreatedCouponRedemption(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("discountuser")
        s = stripe_client()
        self.cust = s.Customer.create(email=self.user.email)
        self.track("customer", self.cust.id)
        self.sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status="active",
        )

    def _discount_obj(self, coupon_id):
        return {
            "coupon": {"id": coupon_id},
            "customer": self.cust.id,
        }

    def test_staff_coupon_creates_redemption(self):
        coupon = Coupon.objects.create(
            code="DISC-TEST-1", percent=10, label="Disc test"
        )
        _handle_discount_created(self._discount_obj("DISC-TEST-1"))
        self.assertTrue(
            CouponRedemption.objects.filter(user=self.user, coupon=coupon).exists()
        )

    def test_staff_coupon_redemption_idempotent(self):
        coupon = Coupon.objects.create(
            code="DISC-TEST-2", percent=10, label="Disc test 2"
        )
        _handle_discount_created(self._discount_obj("DISC-TEST-2"))
        _handle_discount_created(self._discount_obj("DISC-TEST-2"))
        self.assertEqual(
            CouponRedemption.objects.filter(user=self.user, coupon=coupon).count(), 1
        )

    def test_unknown_coupon_does_not_raise(self):
        # Stripe-native coupon — no local record, must silently skip
        _handle_discount_created(self._discount_obj("stripe-native-xyz"))

    def test_referral_code_sets_referred_by(self):
        referrer = make_user("referrer")
        s = stripe_client()
        ref_cust = s.Customer.create(email=referrer.email)
        self.track("customer", ref_cust.id)
        ref_sub = Subscription.objects.create(
            user=referrer,
            stripe_customer_id=ref_cust.id,
            status="active",
            referral_code="NVD-ABCDE",
        )
        _handle_discount_created(self._discount_obj("NVD-ABCDE"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.referred_by, referrer)
