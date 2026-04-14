# billing/tests/views/test_portal_guards.py
from django.test import tag
from django.urls import reverse

from billing.tests.helpers import BillingTestCase, make_subscription, make_user


URL = reverse('billing:portal')


class PortalGuardsTest(BillingTestCase):

    def test_no_subscription_redirects_to_membership(self):
        user = make_user('pg_nosub')
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertRedirects(r, reverse('billing:membership'), fetch_redirect_response=False)

    def test_no_stripe_subscription_id_shows_info_and_redirects(self):
        user = make_user('pg_nosid')
        make_subscription(user, status='active', stripe_customer_id='cus_pg1')
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertRedirects(r, reverse('billing:membership'), fetch_redirect_response=False)

    def test_invalid_customer_id_prefix_shows_error_and_redirects(self):
        user = make_user('pg_badcus')
        sub = make_subscription(user, status='active', stripe_customer_id='bad_id_123')
        sub.stripe_subscription_id = 'sub_fake'
        sub.save()
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertRedirects(r, reverse('billing:membership'), fetch_redirect_response=False)

    @tag('stripe')
    def test_no_such_customer_resets_subscription(self):
        # Use a syntactically valid but non-existent Stripe customer id.
        # stripe.billing_portal.Session.create will raise InvalidRequestError("No such customer").
        user = make_user('pg_reset')
        sub = make_subscription(user, status='active',
                                 stripe_customer_id='cus_nonexistent_nvd_test')
        sub.stripe_subscription_id = 'sub_pg_gone'
        sub.save()
        self.client.force_login(user)
        r = self.client.get(URL)
        sub.refresh_from_db()
        self.assertIsNone(sub.stripe_subscription_id)
        self.assertEqual(sub.status, 'cancelled')
        self.assertRedirects(r, reverse('billing:membership'), fetch_redirect_response=False)
