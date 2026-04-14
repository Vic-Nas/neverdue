# billing/tests/views/test_generate_referral_code.py
from django.test import TestCase
from django.urls import reverse

from billing.models import Coupon
from billing.tests.helpers import make_subscription, make_user


URL = reverse('billing:generate_referral_code')


class GenerateReferralCodeViewTest(TestCase):

    def test_get_returns_405(self):
        user = make_user('grc_get')
        make_subscription(user, status='active')
        self.client.force_login(user)
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 405)

    def test_unauthenticated_post_redirects(self):
        r = self.client.post(URL)
        self.assertEqual(r.status_code, 302)

    def test_free_user_gets_403(self):
        user = make_user('grc_free')
        make_subscription(user, status='cancelled')
        self.client.force_login(user)
        r = self.client.post(URL)
        self.assertEqual(r.status_code, 403)
        self.assertIn('error', r.json())

    def test_pro_user_no_existing_code(self):
        user = make_user('grc_pro')
        make_subscription(user, status='active')
        self.client.force_login(user)
        r = self.client.post(URL)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertRegex(data['code'], r'^NVD-[A-Z0-9]{5}$')
        self.assertEqual(Coupon.objects.filter(head=user).count(), 1)

    def test_pro_user_code_already_exists(self):
        user = make_user('grc_exists')
        sub = make_subscription(user, status='active')
        sub.generate_referral_code()
        existing_code = sub.referral_code
        count_before = Coupon.objects.count()
        self.client.force_login(user)
        r = self.client.post(URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['code'], existing_code)
        self.assertEqual(Coupon.objects.count(), count_before)

    def test_staff_user_head_is_none(self):
        user = make_user('grc_staff')
        user.is_staff = True
        user.save()
        make_subscription(user, status='active')
        self.client.force_login(user)
        r = self.client.post(URL)
        self.assertEqual(r.status_code, 200)
        coupon = Coupon.objects.get(code=r.json()['code'])
        self.assertIsNone(coupon.head)
