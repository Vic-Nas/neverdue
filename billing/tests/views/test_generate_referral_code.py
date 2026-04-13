# billing/tests/views/test_generate_referral_code.py
import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse

from billing.models import Coupon, Subscription
from billing.tests.helpers import make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _sub(user, status='active', customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or _cus_id(),
        status=status,
    )


def _coupon(head=None):
    return Coupon.objects.create(
            code=f'GEN{uuid.uuid4().hex[:5].upper()}',
            percent='12.50',
            head=head,
        )


class TestGenerateReferralCodeView(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('billing:generate_referral_code')

    def _post(self, user):
        self.client.force_login(user)
        return self.client.post(self.url)

    def test_generates_code_for_pro_user(self):
        user = make_user('gen_pro')
        _sub(user, 'active')
        response = self._post(user)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('code', data)
        self.assertTrue(data['code'].startswith('NVD-'))

    def test_returns_existing_code_if_already_generated(self):
        user = make_user('gen_existing')
        sub = _sub(user, 'active')
        coupon = _coupon(head=user)
        sub.referral_coupon = coupon
        sub.save(update_fields=['referral_coupon'])
        response = self._post(user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['code'], coupon.code)

    def test_403_for_free_user(self):
        user = make_user('gen_free')
        _sub(user, 'cancelled')
        response = self._post(user)
        self.assertEqual(response.status_code, 403)

    def test_403_for_unauthenticated(self):
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [302, 403])

    def test_get_method_rejected(self):
        user = make_user('gen_get')
        _sub(user, 'active')
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_stripe_error_returns_500(self, mock_push):
        user = make_user('gen_err')
        _sub(user, 'active')
        # Make generate_referral_code blow up
        with patch.object(Subscription, 'generate_referral_code', side_effect=Exception('boom')):
            response = self._post(user)
        self.assertEqual(response.status_code, 500)
