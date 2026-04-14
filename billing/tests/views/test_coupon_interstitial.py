# billing/tests/views/test_coupon_interstitial.py
from django.test import TestCase
from django.urls import reverse

from billing.tests.helpers import make_coupon, make_subscription, make_user


URL = reverse('billing:coupon_interstitial')


class CouponInterstitialViewTest(TestCase):

    def setUp(self):
        self.user = make_user('inter_user')
        make_subscription(self.user, status='cancelled')
        self.client.force_login(self.user)

    def test_get_clears_session_and_renders(self):
        session = self.client.session
        session['pending_coupon_code'] = 'OLD'
        session.save()
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('pending_coupon_code', self.client.session)

    def test_post_skip_clears_session_and_redirects(self):
        session = self.client.session
        session['pending_coupon_code'] = 'OLD'
        session.save()
        r = self.client.post(URL, {'action': 'skip'})
        self.assertRedirects(r, reverse('billing:checkout'), fetch_redirect_response=False)
        self.assertNotIn('pending_coupon_code', self.client.session)

    def test_post_confirm_redirects_to_checkout(self):
        r = self.client.post(URL, {'action': 'confirm'})
        self.assertRedirects(r, reverse('billing:checkout'), fetch_redirect_response=False)

    def test_post_lookup_no_code_shows_error(self):
        r = self.client.post(URL, {'action': 'lookup', 'code': ''})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Please enter a code.')

    def test_post_lookup_unknown_code_shows_error(self):
        r = self.client.post(URL, {'action': 'lookup', 'code': 'NOPE00'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Code not found.')

    def test_post_lookup_valid_code_sets_session_and_renders_coupon(self):
        head = make_user('inter_head')
        make_subscription(head, status='active')
        coupon = make_coupon(head=head, code='VALID1', percent='12.50')
        r = self.client.post(URL, {'action': 'lookup', 'code': 'VALID1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.session.get('pending_coupon_code'), 'VALID1')
        ctx_coupon = r.context['coupon']
        self.assertEqual(ctx_coupon['percent'], 12.5)
        self.assertIn('head_active', ctx_coupon)
        self.assertIn('head_is_none', ctx_coupon)
        self.assertIn('redeemer_count', ctx_coupon)
