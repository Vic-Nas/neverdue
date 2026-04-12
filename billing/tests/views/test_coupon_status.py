# billing/tests/views/test_coupon_status.py
import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse

from billing.models import Coupon, Subscription
from billing.tests.helpers import make_user

import stripe


def _coupon(code=None, head=None, max_redemptions=None):
    with patch.object(Coupon, '_push_to_stripe'):
        return Coupon.objects.create(
            code=code or f'CS{uuid.uuid4().hex[:6].upper()}',
            percent='12.50',
            max_redemptions=max_redemptions,
            head=head,
        )


def _sub(user, status='active'):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=f'cus_{uuid.uuid4().hex[:10]}',
        status=status,
    )


def _fake_promo(code, times_redeemed=0, max_redemptions=None, active=True):
    p = MagicMock()
    p.get.side_effect = lambda k, default=None: {
        'times_redeemed': times_redeemed,
        'max_redemptions': max_redemptions,
        'active': active,
    }.get(k, default)
    return p


class TestCouponStatusView(TestCase):

    def setUp(self):
        self.client = Client()

    def _get(self, code):
        return self.client.get(reverse('billing:coupon_status', args=[code]))

    def _mock_promo_list(self, promo):
        return patch('billing.views.pages.stripe.PromotionCode.list',
                     return_value=MagicMock(data=[promo]))

    def test_valid_code_with_active_head(self):
        head = make_user('head_cs')
        _sub(head, 'active')
        coupon = _coupon(code='CSACTIVE', head=head)
        promo = _fake_promo('CSACTIVE')
        with self._mock_promo_list(promo):
            response = self._get('CSACTIVE')
        self.assertTrue(response.context['valid'])
        self.assertTrue(response.context['head_active'])

    def test_valid_code_with_cancelled_head(self):
        head = make_user('head_cs2')
        _sub(head, 'cancelled')
        coupon = _coupon(code='CSCANC', head=head)
        promo = _fake_promo('CSCANC')
        with self._mock_promo_list(promo):
            response = self._get('CSCANC')
        self.assertTrue(response.context['valid'])
        self.assertFalse(response.context['head_active'])

    def test_valid_code_head_none_shows_neverdue(self):
        """head=None → head_label='NeverDue', head_active=True."""
        coupon = _coupon(code='CSNVD', head=None)
        promo = _fake_promo('CSNVD')
        with self._mock_promo_list(promo):
            response = self._get('CSNVD')
        self.assertTrue(response.context['valid'])
        self.assertEqual(response.context['head_label'], 'NeverDue')
        self.assertTrue(response.context['head_active'])

    def test_unknown_code_not_in_db(self):
        response = self._get('NOTEXIST')
        self.assertFalse(response.context['valid'])

    def test_code_in_db_but_not_on_stripe(self):
        """PromotionCode.list returns empty — valid=False."""
        coupon = _coupon(code='CSNOSTRIPE')
        with patch('billing.views.pages.stripe.PromotionCode.list',
                   return_value=MagicMock(data=[])):
            response = self._get('CSNOSTRIPE')
        self.assertFalse(response.context['valid'])

    def test_unlimited_max_redemptions(self):
        coupon = _coupon(code='CSUNLIM', max_redemptions=None)
        promo = _fake_promo('CSUNLIM', max_redemptions=None)
        with self._mock_promo_list(promo):
            response = self._get('CSUNLIM')
        self.assertIsNone(response.context['remaining'])

    def test_slots_remaining_computed(self):
        """used=3, max=12 → remaining=9."""
        coupon = _coupon(code='CSSLOTS', max_redemptions=12)
        promo = _fake_promo('CSSLOTS', times_redeemed=3, max_redemptions=12)
        with self._mock_promo_list(promo):
            response = self._get('CSSLOTS')
        self.assertEqual(response.context['remaining'], 9)

    def test_stripe_error_returns_502(self):
        coupon = _coupon(code='CSERR')
        with patch('billing.views.pages.stripe.PromotionCode.list',
                   side_effect=stripe.error.StripeError('fail')):
            response = self._get('CSERR')
        self.assertEqual(response.status_code, 502)

    def test_unauthenticated_access_allowed(self):
        """No login required for coupon_status."""
        coupon = _coupon(code='CSANON')
        promo = _fake_promo('CSANON')
        with self._mock_promo_list(promo):
            response = self._get('CSANON')
        self.assertNotIn(response.status_code, [301, 302])
