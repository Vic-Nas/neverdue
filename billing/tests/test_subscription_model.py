# billing/tests/test_subscription_model.py
"""
Subscription model tests — creation, is_pro, Stripe customer lifecycle,
and status sync accuracy.
"""
from billing.models import Subscription
from billing.tests.helpers import BillingTestCase, make_user, stripe_client


class SubscriptionIsPro(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("subuser")

    def _make_sub(self, status):
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        return Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status=status,
        )

    def test_active_is_pro(self):
        sub = self._make_sub("active")
        self.assertTrue(sub.is_pro)

    def test_trialing_is_pro(self):
        sub = self._make_sub("trialing")
        self.assertTrue(sub.is_pro)

    def test_cancelled_not_pro(self):
        sub = self._make_sub("cancelled")
        self.assertFalse(sub.is_pro)

    def test_past_due_not_pro(self):
        sub = self._make_sub("past_due")
        self.assertFalse(sub.is_pro)


class StripeCustomerCreation(BillingTestCase):
    def test_customer_created_with_correct_email(self):
        user = make_user("custcheck", email="custcheck@example.com")
        s = stripe_client()
        cust = s.Customer.create(email=user.email)
        self.track("customer", cust.id)

        sub = Subscription.objects.create(
            user=user,
            stripe_customer_id=cust.id,
            status="cancelled",
        )
        self.assertEqual(sub.stripe_customer_id, cust.id)

        retrieved = s.Customer.retrieve(cust.id)
        self.assertEqual(retrieved.email, "custcheck@example.com")

    def test_subscription_onetoone_enforced(self):
        user = make_user("onetoonechk")
        s = stripe_client()
        cust = s.Customer.create(email=user.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=user,
            stripe_customer_id=cust.id,
            status="cancelled",
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            cust2 = s.Customer.create(email=user.email)
            self.track("customer", cust2.id)
            Subscription.objects.create(
                user=user,
                stripe_customer_id=cust2.id,
                status="cancelled",
            )
