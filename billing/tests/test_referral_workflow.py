# billing/tests/test_referral_workflow.py
"""
Full referral workflow:
  1. Pro user generates a referral code (NVD-XXXXX).
  2. Code is NOT synced to Stripe (it's applied as a Stripe coupon that the
     referrer must have created separately — or via _push_combined_discount).
  3. New user signs up with referral code at checkout.
  4. customer.discount.created fires with coupon id == referral code.
  5. _handle_discount_created sets new user's referred_by = referrer.
  6. Once referred user's sub goes active, compute_discount includes 12.5% for referrer.
  7. Referral code applied twice by same user is idempotent (referred_by not overwritten).
"""
from billing.discount import compute_discount
from billing.models import Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, make_user, s,
)
from billing.views.webhook import _handle_discount_created


class ReferralCodeGeneration(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.referrer = make_user('referrer')
        cust = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust.id)
        self.referrer_sub = Subscription.objects.create(
            user=self.referrer,
            stripe_customer_id=cust.id,
            status='active',
        )

    def test_generate_code_format(self):
        code = self.referrer_sub.generate_referral_code()
        self.assertRegex(code, r'^NVD-[A-Z0-9]{5}$')

    def test_generate_code_persisted(self):
        code = self.referrer_sub.generate_referral_code()
        self.referrer_sub.refresh_from_db()
        self.assertEqual(self.referrer_sub.referral_code, code)

    def test_generate_code_unique_across_subscriptions(self):
        code1 = self.referrer_sub.generate_referral_code()
        other = make_user('other_referrer')
        cust2 = create_stripe_customer(other.email, other.username)
        self.track('customer', cust2.id)
        sub2 = Subscription.objects.create(
            user=other,
            stripe_customer_id=cust2.id,
            status='active',
        )
        code2 = sub2.generate_referral_code()
        self.assertNotEqual(code1, code2)


class ReferralCheckoutWebhook(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.referrer = make_user('ref_referrer')
        cust_r = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust_r.id)
        self.referrer_sub = Subscription.objects.create(
            user=self.referrer,
            stripe_customer_id=cust_r.id,
            status='active',
            referral_code='NVD-TESTA',
        )

        self.new_user = make_user('ref_newuser')
        cust_n = create_stripe_customer(self.new_user.email, self.new_user.username)
        self.track('customer', cust_n.id)
        self.new_sub = Subscription.objects.create(
            user=self.new_user,
            stripe_customer_id=cust_n.id,
            status='cancelled',
        )

    def _discount_obj(self, coupon_id):
        return {'coupon': {'id': coupon_id}, 'customer': self.new_sub.stripe_customer_id}

    def test_discount_created_with_referral_sets_referred_by(self):
        _handle_discount_created(self._discount_obj('NVD-TESTA'))
        self.new_user.refresh_from_db()
        self.assertEqual(self.new_user.referred_by, self.referrer)

    def test_referred_by_not_overwritten_on_second_event(self):
        _handle_discount_created(self._discount_obj('NVD-TESTA'))
        other = make_user('other_ref')
        cust_o = create_stripe_customer(other.email, other.username)
        self.track('customer', cust_o.id)
        Subscription.objects.create(
            user=other,
            stripe_customer_id=cust_o.id,
            status='active',
            referral_code='NVD-TESTB',
        )
        _handle_discount_created(self._discount_obj('NVD-TESTB'))
        self.new_user.refresh_from_db()
        # Still points to original referrer
        self.assertEqual(self.new_user.referred_by, self.referrer)

    def test_unknown_referral_code_treated_as_stripe_native(self):
        """Code not in any referral_code, not in Coupon table -> silent ignore."""
        _handle_discount_created(self._discount_obj('NVD-ZZZZZ'))
        self.new_user.refresh_from_db()
        self.assertIsNone(self.new_user.referred_by)


class ReferralDiscount(BillingTestCase):

    def setUp(self):
        super().setUp()
        self.referrer = make_user('ref_disc_referrer')
        cust = create_stripe_customer(self.referrer.email, self.referrer.username)
        self.track('customer', cust.id)
        Subscription.objects.create(
            user=self.referrer,
            stripe_customer_id=cust.id,
            status='active',
            referral_code='NVD-DISC1',
        )

    def _make_referred(self, username, status):
        u = make_user(username)
        cust = create_stripe_customer(u.email, u.username)
        self.track('customer', cust.id)
        Subscription.objects.create(
            user=u, stripe_customer_id=cust.id, status=status
        )
        u.referred_by = self.referrer
        u.save()
        return u

    def test_trialing_referred_user_does_not_count(self):
        self._make_referred('ref_trial', 'trialing')
        self.assertEqual(compute_discount(self.referrer), 0)

    def test_active_referred_user_adds_12_percent(self):
        self._make_referred('ref_active', 'active')
        self.assertEqual(compute_discount(self.referrer), 12)

    def test_two_active_referrals_adds_25_percent(self):
        self._make_referred('ref_a1', 'active')
        self._make_referred('ref_a2', 'active')
        # 2 * 12.5 = 25
        self.assertEqual(compute_discount(self.referrer), 25)

    def test_cancelled_referred_user_does_not_count(self):
        self._make_referred('ref_cancel', 'cancelled')
        self.assertEqual(compute_discount(self.referrer), 0)
