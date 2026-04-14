# billing/tests/views/test_success_cancel.py
from django.test import TestCase
from django.urls import reverse

from billing.tests.helpers import make_user


class SuccessCancelViewTest(TestCase):

    def test_success_authenticated_200(self):
        user = make_user('sc_auth1')
        self.client.force_login(user)
        r = self.client.get(reverse('billing:success'))
        self.assertEqual(r.status_code, 200)

    def test_cancel_authenticated_200(self):
        user = make_user('sc_auth2')
        self.client.force_login(user)
        r = self.client.get(reverse('billing:cancel'))
        self.assertEqual(r.status_code, 200)

    def test_success_unauthenticated_redirects(self):
        url = reverse('billing:success')
        r = self.client.get(url)
        self.assertRedirects(r, f"{reverse('accounts:login')}?next={url}",
                             fetch_redirect_response=False)

    def test_cancel_unauthenticated_redirects(self):
        url = reverse('billing:cancel')
        r = self.client.get(url)
        self.assertRedirects(r, f"{reverse('accounts:login')}?next={url}",
                             fetch_redirect_response=False)