# billing/tests/views/test_plans.py
from django.test import TestCase
from django.urls import reverse

from billing.models import compute_discount
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


class PlansViewTest(TestCase):

    def test_unauthenticated_redirects(self):
        r = self.client.get(reverse('billing:membership'))
        self.assertRedirects(r, f"{reverse('accounts:login')}?next={reverse('billing:membership')}",
                             fetch_redirect_response=False)

    def test_free_user_context(self):
        user = make_user('free1')
        self.client.force_login(user)
        r = self.client.get(reverse('billing:membership'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['is_pro'])
        self.assertEqual(r.context['discount'], 0)
        self.assertFalse(r.context['show_referral'])

    def test_pro_user_context(self):
        user = make_user('pro1')
        sub = make_subscription(user, status='active')
        sub.generate_referral_code()
        self.client.force_login(user)
        r = self.client.get(reverse('billing:membership'))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['is_pro'])
        self.assertTrue(r.context['show_referral'])
        self.assertEqual(r.context['discount'], compute_discount(user))

    def test_pro_user_with_two_active_partners(self):
        head = make_user('pro_head1')
        sub = make_subscription(head, status='active')
        sub.generate_referral_code()
        coupon = sub.referral_coupon
        for i in range(2):
            rd = make_user(f'rd_plans_{i}')
            make_subscription(rd, status='active')
            make_redemption(coupon, rd)
        self.client.force_login(head)
        r = self.client.get(reverse('billing:membership'))
        self.assertEqual(r.context['active_partners'], 2)

    def test_pro_user_no_referral_coupon(self):
        user = make_user('pro_noref')
        make_subscription(user, status='active')
        self.client.force_login(user)
        r = self.client.get(reverse('billing:membership'))
        self.assertEqual(r.context['active_partners'], 0)
        self.assertIsNone(r.context['referral_code'])