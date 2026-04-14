# billing/tests/views/test_portal_stripe.py
from django.test import tag
from django.urls import reverse

from billing.tests.helpers import BillingTestCase, create_stripe_customer, make_subscription, make_user


URL = reverse('billing:portal')


@tag('stripe')
class PortalStripeTest(BillingTestCase):

    def _make_sub_with_real_customer(self, username):
        user = make_user(username)
        cust = create_stripe_customer(user.email, user.username)
        self.track('customer', cust.id)
        sub = make_subscription(user, status='active', stripe_customer_id=cust.id)
        sub.stripe_subscription_id = 'sub_fake_portal'
        sub.save()
        return user

    def test_valid_customer_redirects_to_billing_portal(self):
        user = self._make_sub_with_real_customer('portal_stripe1')
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 302)
        self.assertIn('billing.stripe.com', r['Location'])

    def test_deleted_customer_redirects_to_membership(self):
        # Create a real customer, immediately delete them, then attempt portal.
        # Stripe raises InvalidRequestError("No such customer") which the view
        # catches in the StripeError branch → redirects to billing:membership.
        user = make_user('portal_stripe2')
        from billing.tests.helpers import s
        cust = create_stripe_customer(user.email, user.username)
        s().Customer.delete(cust.id)  # hard-delete so portal call fails
        sub = make_subscription(user, status='active', stripe_customer_id=cust.id)
        sub.stripe_subscription_id = 'sub_fake_deleted'
        sub.save()
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertRedirects(r, reverse('billing:membership'), fetch_redirect_response=False)
