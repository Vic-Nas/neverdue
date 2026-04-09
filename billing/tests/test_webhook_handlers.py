# billing/tests/test_webhook_handlers.py
"""
Tests for webhook handler edge cases not covered in test_webhook.py:
- _sync_subscription: retry_jobs NOT triggered active->active
- _sync_subscription: missing period_end items handled
- _handle_invoice_upcoming: dispatches discount push for pro, skips non-pro
- _handle_checkout_completed: promo attach, no-op paths
And discount edge cases:
- expired coupon redemption still counts toward discount
- referral_summary masked email format
- referral_summary counts flag
"""
from unittest.mock import patch, call

from billing.discount import compute_discount, referral_summary
from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import BillingTestCase, make_user, stripe_client
from billing.views.webhook import (
    _handle_checkout_completed,
    _handle_invoice_upcoming,
    _sync_subscription,
)


# ---------------------------------------------------------------------------
# _sync_subscription edge cases
# ---------------------------------------------------------------------------

class SyncSubscriptionEdgeCases(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("syncedge")
        s = stripe_client()
        self.cust = s.Customer.create(email=self.user.email)
        self.track("customer", self.cust.id)
        self.sub = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status="active",
        )

    def _stripe_sub(self, status, items=None):
        obj = {
            "id": "sub_edge1",
            "customer": self.cust.id,
            "status": status,
            "items": items if items is not None else {
                "data": [{"current_period_end": 9999999999}]
            },
        }
        return obj

    def test_active_to_active_does_not_defer_retry_jobs(self):
        """retry_jobs_after_plan_upgrade must only fire on non-active -> active."""
        with patch('billing.views.webhook._push_combined_discount'), \
             patch('emails.tasks.retry_jobs_after_plan_upgrade') as mock_retry:
            _sync_subscription(self._stripe_sub("active"))
            mock_retry.defer.assert_not_called()

    def test_cancelled_to_active_defers_retry_jobs(self):
        self.sub.status = "cancelled"
        self.sub.save()
        with patch('billing.views.webhook._push_combined_discount'), \
             patch('emails.tasks.retry_jobs_after_plan_upgrade') as mock_retry:
            _sync_subscription(self._stripe_sub("active"))
            mock_retry.defer.assert_called_once_with(user_id=self.user.pk)

    def test_missing_items_key_does_not_raise(self):
        """period_end extraction falls back gracefully if items missing."""
        stripe_sub = self._stripe_sub("active", items={})
        # Should not raise, period_end just stays None
        _sync_subscription(stripe_sub)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")

    def test_empty_items_data_does_not_raise(self):
        stripe_sub = self._stripe_sub("active", items={"data": []})
        _sync_subscription(stripe_sub)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")


# ---------------------------------------------------------------------------
# _handle_invoice_upcoming
# ---------------------------------------------------------------------------

class InvoiceUpcomingHandler(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("invupcoming")
        s = stripe_client()
        self.cust = s.Customer.create(email=self.user.email)
        self.track("customer", self.cust.id)

    def test_pro_sub_triggers_push_combined_discount(self):
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status="active",
        )
        with patch('billing.views.webhook._push_combined_discount') as mock_push:
            _handle_invoice_upcoming({"customer": self.cust.id})
            mock_push.assert_called_once()

    def test_non_pro_sub_skips_push(self):
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            status="cancelled",
        )
        with patch('billing.views.webhook._push_combined_discount') as mock_push:
            _handle_invoice_upcoming({"customer": self.cust.id})
            mock_push.assert_not_called()

    def test_unknown_customer_does_not_raise(self):
        _handle_invoice_upcoming({"customer": "cus_doesnotexist"})

    def test_missing_customer_key_does_not_raise(self):
        _handle_invoice_upcoming({})


# ---------------------------------------------------------------------------
# _handle_checkout_completed
# ---------------------------------------------------------------------------

class CheckoutCompletedHandler(BillingTestCase):
    def test_no_subscription_id_is_noop(self):
        with patch('stripe.Subscription.modify') as mock_modify:
            _handle_checkout_completed({
                "subscription": None,
                "discounts": [{"coupon": "PROMO1"}],
            })
            mock_modify.assert_not_called()

    def test_no_discounts_is_noop(self):
        with patch('stripe.Subscription.modify') as mock_modify:
            _handle_checkout_completed({
                "subscription": "sub_abc",
                "discounts": [],
            })
            mock_modify.assert_not_called()

    def test_promotion_code_attached_to_subscription(self):
        with patch('stripe.Subscription.modify') as mock_modify:
            _handle_checkout_completed({
                "subscription": "sub_abc",
                "discounts": [{"promotion_code": "promo_xyz", "coupon": None}],
            })
            mock_modify.assert_called_once_with(
                "sub_abc",
                discounts=[{"promotion_code": "promo_xyz"}],
            )

    def test_coupon_attached_when_no_promo_code(self):
        with patch('stripe.Subscription.modify') as mock_modify:
            _handle_checkout_completed({
                "subscription": "sub_abc",
                "discounts": [{"promotion_code": None, "coupon": "NVD-TESTX"}],
            })
            mock_modify.assert_called_once_with(
                "sub_abc",
                discounts=[{"coupon": "NVD-TESTX"}],
            )

    def test_multiple_discounts_all_attached(self):
        with patch('stripe.Subscription.modify') as mock_modify:
            _handle_checkout_completed({
                "subscription": "sub_abc",
                "discounts": [
                    {"promotion_code": "promo_1", "coupon": None},
                    {"promotion_code": None, "coupon": "NVD-REF"},
                ],
            })
            mock_modify.assert_called_once_with(
                "sub_abc",
                discounts=[
                    {"promotion_code": "promo_1"},
                    {"coupon": "NVD-REF"},
                ],
            )

    def test_stripe_error_logged_not_raised(self):
        import stripe as stripe_mod
        with patch('stripe.Subscription.modify',
                   side_effect=stripe_mod.error.StripeError("fail")):
            # Must not propagate
            _handle_checkout_completed({
                "subscription": "sub_abc",
                "discounts": [{"coupon": "X"}],
            })


# ---------------------------------------------------------------------------
# Discount edge cases
# ---------------------------------------------------------------------------

class DiscountEdgeCases(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("discedge")

    def test_expired_coupon_redemption_still_counts(self):
        """
        CouponRedemption persists even after the coupon expires.
        compute_discount must still count it (the model comment says so).
        """
        from django.utils import timezone
        from datetime import timedelta
        coupon = Coupon.objects.create(
            code="EXP-STILL",
            percent=25,
            label="Expired",
            expires_at=timezone.now() - timedelta(days=1),
        )
        CouponRedemption.objects.create(user=self.user, coupon=coupon)
        self.assertEqual(compute_discount(self.user), 25)


class ReferralSummary(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.referrer = make_user("sumreferrer")

    def _referred_user(self, username, status):
        u = make_user(username, email=f"{username}@example.com")
        s = stripe_client()
        cust = s.Customer.create(email=u.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=u,
            stripe_customer_id=cust.id,
            status=status,
        )
        u.referred_by = self.referrer
        u.save()
        return u

    def test_active_referred_counts_toward_discount(self):
        self._referred_user("refactive", "active")
        summary = referral_summary(self.referrer)
        self.assertEqual(len(summary), 1)
        self.assertTrue(summary[0]["counts"])

    def test_trialing_referred_does_not_count(self):
        self._referred_user("reftrial", "trialing")
        summary = referral_summary(self.referrer)
        self.assertFalse(summary[0]["counts"])

    def test_cancelled_referred_does_not_count(self):
        self._referred_user("refcancel", "cancelled")
        summary = referral_summary(self.referrer)
        self.assertFalse(summary[0]["counts"])

    def test_masked_email_format(self):
        make_user("masktest", email="johndoe@gmail.com")
        from billing.discount import _mask_email
        masked = _mask_email("johndoe@gmail.com")
        # Local: jo***e, domain: gm***, tld: com
        self.assertRegex(masked, r'^jo\*\*\*e@gm\*\*\*\.com$')

    def test_masked_email_short_local(self):
        from billing.discount import _mask_email
        masked = _mask_email("ab@x.io")
        self.assertIn("***", masked)

    def test_no_referrals_returns_empty(self):
        summary = referral_summary(self.referrer)
        self.assertEqual(summary, [])
