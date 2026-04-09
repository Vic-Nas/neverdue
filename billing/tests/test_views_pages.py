# billing/tests/test_views_pages.py
"""
Billing page view tests — authentication gates, redirect behaviour,
checkout session creation, portal session.
No Stripe subscription needed for most; creates real Stripe customers.
"""
from unittest.mock import patch

import stripe
from django.test import Client, TestCase
from django.urls import reverse

from billing.models import Subscription
from billing.tests.helpers import BillingTestCase, make_user, stripe_client


class UnauthenticatedRedirects(TestCase):
    def setUp(self):
        self.client = Client()

    def _assert_redirects_to_login(self, url_name):
        r = self.client.get(reverse(f"billing:{url_name}"))
        self.assertIn(r.status_code, (302, 301))

    def test_plans_requires_login(self):
        self._assert_redirects_to_login("plans")

    def test_checkout_requires_login(self):
        self._assert_redirects_to_login("checkout")

    def test_portal_requires_login(self):
        self._assert_redirects_to_login("portal")

    def test_success_requires_login(self):
        self._assert_redirects_to_login("success")

    def test_cancel_requires_login(self):
        self._assert_redirects_to_login("cancel")


class PlansView(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("plansuser")
        self.client = Client()
        self.client.force_login(self.user)

    def test_plans_page_loads_without_subscription(self):
        r = self.client.get(reverse("billing:plans"))
        self.assertEqual(r.status_code, 200)

    def test_plans_page_loads_with_subscription(self):
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status="active",
        )
        r = self.client.get(reverse("billing:plans"))
        self.assertEqual(r.status_code, 200)


class CheckoutView(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("checkoutuser")
        self.client = Client()
        self.client.force_login(self.user)

    def test_checkout_creates_stripe_customer_and_redirects(self):
        fake_session = type("S", (), {"url": "https://checkout.stripe.com/pay/test"})()
        with patch("billing.views.pages.stripe.checkout.Session.create",
                   return_value=fake_session):
            r = self.client.get(reverse("billing:checkout"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("stripe.com", r["Location"])
        sub = Subscription.objects.get(user=self.user)
        self.assertTrue(sub.stripe_customer_id.startswith("cus_"))
        self.track("customer", sub.stripe_customer_id)

    def test_checkout_reuses_existing_customer(self):
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status="cancelled",
        )
        fake_session = type("S", (), {"url": "https://checkout.stripe.com/pay/test"})()
        with patch("billing.views.pages.stripe.checkout.Session.create",
                   return_value=fake_session):
            r = self.client.get(reverse("billing:checkout"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)


class PortalView(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("portaluser")
        self.client = Client()
        self.client.force_login(self.user)

    def test_portal_redirects_to_plans_without_subscription(self):
        r = self.client.get(reverse("billing:portal"))
        self.assertRedirects(r, reverse("billing:plans"), fetch_redirect_response=False)

    def test_portal_redirects_to_plans_without_stripe_subscription(self):
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status="cancelled",
            stripe_subscription_id=None,
        )
        r = self.client.get(reverse("billing:portal"))
        self.assertRedirects(r, reverse("billing:plans"), fetch_redirect_response=False)

    def test_portal_redirects_to_stripe_for_active_sub(self):
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            stripe_subscription_id="sub_fake",
            status="active",
        )
        r = self.client.get(reverse("billing:portal"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("stripe.com", r["Location"])


class ReferralCodeView(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("refcodeuser")
        self.client = Client()
        self.client.force_login(self.user)

    def test_generate_referral_code_requires_post(self):
        r = self.client.get(reverse("billing:generate_referral_code"))
        self.assertEqual(r.status_code, 405)

    def test_generate_referral_code_requires_pro(self):
        r = self.client.post(reverse("billing:generate_referral_code"))
        self.assertEqual(r.status_code, 403)

    def test_generate_referral_code_returns_code(self):
        s = stripe_client()
        cust = s.Customer.create(email=self.user.email)
        self.track("customer", cust.id)
        Subscription.objects.create(
            user=self.user,
            stripe_customer_id=cust.id,
            status="active",
        )
        r = self.client.post(reverse("billing:generate_referral_code"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("code", data)
        self.assertTrue(data["code"].startswith("NVD-"))
