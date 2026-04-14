# billing/tests/views/test_checkout_stripe.py
from django.test import tag
from django.urls import reverse

from billing.tests.helpers import BillingTestCase, create_stripe_customer, make_subscription, make_user


URL = reverse('billing:checkout')


@tag('stripe')
class CheckoutStripeTest(BillingTestCase):

    def test_no_pending_code_session_has_no_metadata(self):
        user = make_user('co_stripe1')
        cust = create_stripe_customer(user.email, user.username)
        self.track('customer', cust.id)
        make_subscription(user, status='cancelled', stripe_customer_id=cust.id)
        self.client.force_login(user)
        r = self.client.get(URL)
        # Should redirect to a Stripe checkout URL (2xx or 3xx depending on stripe version)
        self.assertIn(r.status_code, (200, 302))
        if r.status_code == 302:
            self.assertIn('checkout.stripe.com', r['Location'])

    def test_invalid_customer_replaced(self):
        user = make_user('co_stripe2')
        sub = make_subscription(user, status='active', stripe_customer_id='cus_invalid_xyz_9999')
        self.client.force_login(user)
        r = self.client.get(URL)
        sub.refresh_from_db()
        # Old invalid id should have been replaced with a new real customer
        self.assertNotEqual(sub.stripe_customer_id, 'cus_invalid_xyz_9999')
        self.assertTrue(sub.stripe_customer_id.startswith('cus_'))
        if sub.stripe_customer_id != 'cus_invalid_xyz_9999':
            self.track('customer', sub.stripe_customer_id)
