# billing/tests/views/test_plans_view.py
import uuid
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import make_user


def _cus_id():
    return f'cus_{uuid.uuid4().hex[:10]}'


def _sub(user, status='active', customer_id=None):
    return Subscription.objects.create(
        user=user,
        stripe_customer_id=customer_id or _cus_id(),
        status=status,
    )


def _coupon(head=None, percent='12.50'):
    return Coupon.objects.create(
            code=f'PV{uuid.uuid4().hex[:6].upper()}',
            percent=percent,
            head=head,
        )


class TestPlansView(TestCase):

    def setUp(self):
        self.client = Client()

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('billing:membership'))

    def test_discount_in_context_for_pro_user(self):
        user = make_user('pro_disc')
        _sub(user, 'active')
        response = self._get(user)
        self.assertIn('discount', response.context)

    def test_discount_zero_for_free_user(self):
        user = make_user('free_disc')
        _sub(user, 'cancelled')
        response = self._get(user)
        self.assertEqual(response.context['discount'], 0)

    def test_active_partners_count_correct(self):
        head = make_user('head_pv')
        head_sub = _sub(head, 'active')
        coupon = _coupon(head=head)
        head_sub.referral_coupon = coupon
        head_sub.save(update_fields=['referral_coupon'])
        # 2 active redeemers
        for i in range(2):
            r = make_user(f'pv_r{i}')
            _sub(r, 'active')
            CouponRedemption.objects.create(coupon=coupon, user=r)

        response = self._get(head)
        self.assertEqual(response.context['active_partners'], 2)

    def test_active_partners_excludes_cancelled_redeemers(self):
        head = make_user('head_excl')
        head_sub = _sub(head, 'active')
        coupon = _coupon(head=head)
        head_sub.referral_coupon = coupon
        head_sub.save(update_fields=['referral_coupon'])
        r_active = make_user('pv_ra')
        r_cancelled = make_user('pv_rc')
        _sub(r_active, 'active')
        _sub(r_cancelled, 'cancelled')
        CouponRedemption.objects.create(coupon=coupon, user=r_active)
        CouponRedemption.objects.create(coupon=coupon, user=r_cancelled)

        response = self._get(head)
        self.assertEqual(response.context['active_partners'], 1)

    def test_show_referral_true_when_pro(self):
        user = make_user('pv_pro_ref')
        _sub(user, 'active')
        response = self._get(user)
        self.assertTrue(response.context['show_referral'])

    def test_show_referral_true_when_referral_coupon_exists_but_not_pro(self):
        user = make_user('pv_ref_notpro')
        sub = _sub(user, 'cancelled')
        coupon = _coupon(head=user)
        sub.referral_coupon = coupon
        sub.save(update_fields=['referral_coupon'])
        response = self._get(user)
        self.assertTrue(response.context['show_referral'])

    def test_show_referral_false_when_free_no_code(self):
        user = make_user('pv_free_no')
        _sub(user, 'cancelled')
        response = self._get(user)
        self.assertFalse(response.context['show_referral'])

    def test_referral_code_none_when_not_generated(self):
        user = make_user('pv_nocode')
        _sub(user, 'active')
        response = self._get(user)
        self.assertIsNone(response.context['referral_code'])

    def test_referral_code_in_context_when_generated(self):
        user = make_user('pv_hascode')
        sub = _sub(user, 'active')
        coupon = _coupon(head=user)
        sub.referral_coupon = coupon
        sub.save(update_fields=['referral_coupon'])
        response = self._get(user)
        self.assertEqual(response.context['referral_code'], coupon.code)

    def test_unauthenticated_redirects(self):
        response = self.client.get(reverse('billing:membership'))
        self.assertIn(response.status_code, [301, 302])
