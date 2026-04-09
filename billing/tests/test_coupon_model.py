# billing/tests/test_coupon_model.py
"""
Coupon model tests — covers the 'deleted coupon stays on Stripe' and
'rename does nothing on Stripe' bugs you hit.

Key facts about Stripe coupons:
  - id is immutable; you CANNOT rename a coupon, you must delete+recreate.
  - Deleting a local Coupon does NOT touch Stripe (no signal/override).
  - sync_to_stripe() only runs on first admin save — never on update.
  - A coupon cannot be applied once deleted on Stripe, even if local record exists.
"""
import stripe
from django.conf import settings

from billing.models import Coupon
from billing.tests.helpers import BillingTestCase, stripe_client


class CouponSyncCreatesOnStripe(BillingTestCase):
    def test_sync_to_stripe_creates_coupon(self):
        coupon = Coupon(code="TEST-SYNC-1", percent=20, label="Test 20%")
        coupon.save()
        coupon.sync_to_stripe()
        self.track("coupon", coupon.code)

        s = stripe_client()
        sc = s.Coupon.retrieve(coupon.code)
        self.assertEqual(sc.percent_off, 20)
        self.assertEqual(sc.id, "TEST-SYNC-1")

    def test_sync_to_stripe_idempotent_raises_on_duplicate(self):
        """Stripe raises if you try to create the same coupon id twice."""
        coupon = Coupon(code="TEST-SYNC-2", percent=10, label="Dup test")
        coupon.save()
        coupon.sync_to_stripe()
        self.track("coupon", coupon.code)

        with self.assertRaises(stripe.error.InvalidRequestError):
            coupon.sync_to_stripe()  # second call must fail


class CouponDeleteBehaviour(BillingTestCase):
    """
    KNOWN GAP: deleting a local Coupon does NOT delete it from Stripe.
    This test documents the current behaviour so we know it needs fixing.
    After the fix, flip the assertion.
    """

    def test_local_delete_does_not_remove_from_stripe(self):
        coupon = Coupon(code="TEST-DEL-1", percent=15, label="Delete test")
        coupon.save()
        coupon.sync_to_stripe()
        self.track("coupon", coupon.code)

        coupon.delete()  # local only

        # Stripe coupon still exists — this is the bug
        s = stripe_client()
        sc = s.Coupon.retrieve("TEST-DEL-1")
        self.assertEqual(sc.id, "TEST-DEL-1")  # still there — stale on Stripe

    def test_deleted_stripe_coupon_cannot_be_applied(self):
        """After Stripe deletion the coupon is rejected at checkout."""
        code = "TEST-DEL-APPLY"
        coupon = Coupon(code=code, percent=10, label="Del apply")
        coupon.save()
        coupon.sync_to_stripe()

        s = stripe_client()
        s.Coupon.delete(code)  # actually delete on Stripe
        coupon.delete()

        with self.assertRaises(stripe.error.InvalidRequestError):
            s.Coupon.retrieve(code)


class CouponRenameBehaviour(BillingTestCase):
    """
    Stripe coupon IDs are immutable. 'Renaming' (changing code field) locally
    creates a mismatch: Stripe still has the old id, local has a new code.
    The new code won't work because it was never synced.
    """

    def test_renamed_code_is_unknown_to_stripe(self):
        coupon = Coupon(code="TEST-RENAME-OLD", percent=25, label="Rename me")
        coupon.save()
        coupon.sync_to_stripe()
        self.track("coupon", "TEST-RENAME-OLD")

        # Simulate staff 'renaming' by changing the code field locally
        coupon.code = "TEST-RENAME-NEW"
        coupon.save()

        s = stripe_client()
        # Old id still on Stripe
        old = s.Coupon.retrieve("TEST-RENAME-OLD")
        self.assertEqual(old.id, "TEST-RENAME-OLD")

        # New code doesn't exist on Stripe
        with self.assertRaises(stripe.error.InvalidRequestError):
            s.Coupon.retrieve("TEST-RENAME-NEW")

    def test_coupon_created_with_id_equal_to_code(self):
        """
        When a coupon is created, Stripe uses `code` as the coupon id.
        Stripe's coupon id == local code — this is the expected mapping.
        """
        coupon = Coupon(code="TEST-ID-CHECK", percent=30, label="ID check")
        coupon.save()
        coupon.sync_to_stripe()
        self.track("coupon", coupon.code)

        s = stripe_client()
        sc = s.Coupon.retrieve("TEST-ID-CHECK")
        self.assertEqual(sc.id, coupon.code)
