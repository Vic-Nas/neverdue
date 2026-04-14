# billing/tests/views/test_checkout_guards.py
from django.test import tag
from django.urls import reverse

from billing.models import Subscription
from billing.tests.helpers import (
    BillingTestCase, create_stripe_customer, make_subscription, make_user,
)


URL = reverse('billing:checkout')


class CheckoutGuardsTest(BillingTestCase):

    def test_unauthenticated_redirects_to_login(self):
        r = self.client.get(URL)
        self.assertRedirects(r, f"{reverse('accounts:login')}?next={URL}",
                             fetch_redirect_response=False)

    @tag('stripe')
    def test_no_subscription_creates_customer_and_redirects(self):
        user = make_user('co_nosub')
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 302)
        self.assertIn('checkout.stripe.com', r['Location'])
        sub = Subscription.objects.get(user=user)
        self.track('customer', sub.stripe_customer_id)

    @tag('stripe')
    def test_pending_coupon_code_written_to_metadata_and_cleared(self):
        user = make_user('co_code')
        cust = create_stripe_customer(user.email, user.username)
        self.track('customer', cust.id)
        make_subscription(user, status='cancelled', stripe_customer_id=cust.id)
        session = self.client.session
        session['pending_coupon_code'] = 'MYCODE'
        session.save()
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 302)
        self.assertIn('checkout.stripe.com', r['Location'])
        self.assertNotIn('pending_coupon_code', self.client.session)