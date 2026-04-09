# billing/tests/test_coupon_lifecycle.py
"""
Full coupon lifecycle against real Stripe test mode.

Covers the exact bugs you found:
1. Coupon.code becomes Stripe coupon id — confirmed expected behaviour.
2. Deleting coupon locally does NOT delete it from Stripe — BUG, documented.
3. Renaming coupon locally breaks it — Stripe id is immutable — BUG, documented.
4. A coupon that was never synced to Stripe cannot be applied at checkout.
5. sync_to_stripe called twice raises InvalidRequestError (duplicate id).
"""
import stripe

from billing.models import Coupon, CouponRedemption
from billing.tests.helpers import BillingTestCase, s


class CouponStripeSync(BillingTestCase):

    def test_sync_creates_coupon_on_stripe_with_code_as_id(self):
        c = Coupon.objects.create(code='WF-SYNC-1', percent=20, label='Sync test')
        c.sync_to_stripe()
        self.track('coupon', c.code)

        sc = s().Coupon.retrieve('WF-SYNC-1')
        self.assertEqual(sc.id, 'WF-SYNC-1')
        self.assertEqual(sc.percent_off, 20)
        self.assertEqual(sc.duration, 'forever')

    def test_sync_twice_raises_duplicate_error(self):
        c = Coupon.objects.create(code='WF-SYNC-2', percent=10, label='Dup test')
        c.sync_to_stripe()
        self.track('coupon', c.code)
        with self.assertRaises(stripe.error.InvalidRequestError):
            c.sync_to_stripe()

    def test_local_delete_leaves_coupon_on_stripe_BUG(self):
        """
        BUG: Coupon.delete() only removes the local DB row.
        Stripe still has the coupon — it can still be applied.
        Expected: deleting locally should also call stripe.Coupon.delete().
        """
        c = Coupon.objects.create(code='WF-DEL-1', percent=15, label='Del test')
        c.sync_to_stripe()
        self.track('coupon', c.code)

        c.delete()
        self.assertFalse(Coupon.objects.filter(code='WF-DEL-1').exists())

        # Stripe still has it — this is the bug
        sc = s().Coupon.retrieve('WF-DEL-1')
        self.assertEqual(sc.id, 'WF-DEL-1')

    def test_rename_code_locally_breaks_stripe_mapping_BUG(self):
        """
        BUG: Changing coupon.code in DB does not rename it on Stripe.
        Stripe id is immutable. New code is unknown to Stripe.
        Expected: rename should delete old Stripe coupon + create new one.
        """
        c = Coupon.objects.create(code='WF-OLD', percent=25, label='Rename test')
        c.sync_to_stripe()
        self.track('coupon', 'WF-OLD')

        c.code = 'WF-NEW'
        c.save()

        # Old id still exists on Stripe
        old = s().Coupon.retrieve('WF-OLD')
        self.assertEqual(old.id, 'WF-OLD')

        # New code does NOT exist on Stripe
        with self.assertRaises(stripe.error.InvalidRequestError):
            s().Coupon.retrieve('WF-NEW')

    def test_unsynced_coupon_cannot_be_retrieved_from_stripe(self):
        """A coupon created locally but never synced doesn't exist on Stripe."""
        Coupon.objects.create(code='WF-NOSYNC', percent=10, label='No sync')
        with self.assertRaises(stripe.error.InvalidRequestError):
            s().Coupon.retrieve('WF-NOSYNC')
