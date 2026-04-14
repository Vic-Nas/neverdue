# billing/tests/views/test_coupon_lookup.py
from django.test import TestCase
from django.urls import reverse

from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


URL = reverse('billing:coupon_lookup')


class CouponLookupViewTest(TestCase):

    def test_no_code_param_returns_400(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 400)

    def test_unknown_code_returns_404(self):
        r = self.client.get(URL, {'code': 'NOPE99'})
        self.assertEqual(r.status_code, 404)

    def test_head_none_returns_neverdue_label(self):
        make_coupon(head=None, code='GRANT9')
        r = self.client.get(URL, {'code': 'GRANT9'})
        data = r.json()
        self.assertTrue(data['head_is_none'])
        self.assertTrue(data['head_active'])
        self.assertEqual(data['head_label'], 'NeverDue')

    def test_active_head_returns_member_label(self):
        head = make_user('cl_head1')
        make_subscription(head, status='active')
        make_coupon(head=head, code='ACTIVE9')
        r = self.client.get(URL, {'code': 'ACTIVE9'})
        data = r.json()
        self.assertTrue(data['head_active'])
        self.assertEqual(data['head_label'], 'a NeverDue member')

    def test_cancelled_head_head_active_false(self):
        head = make_user('cl_head2')
        make_subscription(head, status='cancelled')
        make_coupon(head=head, code='CANC99')
        r = self.client.get(URL, {'code': 'CANC99'})
        self.assertFalse(r.json()['head_active'])

    def test_redeemer_count_only_active(self):
        head = make_user('cl_head3')
        make_subscription(head, status='active')
        coupon = make_coupon(head=head, code='COUNT9')
        active_rd = make_user('cl_rd_active')
        trialing_rd = make_user('cl_rd_trial')
        make_subscription(active_rd, status='active')
        make_subscription(trialing_rd, status='trialing')
        make_redemption(coupon, active_rd)
        make_redemption(coupon, trialing_rd)
        r = self.client.get(URL, {'code': 'COUNT9'})
        self.assertEqual(r.json()['redeemer_count'], 1)

    def test_response_has_all_six_keys(self):
        make_coupon(head=None, code='KEYS99')
        r = self.client.get(URL, {'code': 'KEYS99'})
        data = r.json()
        for key in ('code', 'percent', 'head_active', 'head_is_none', 'head_label', 'redeemer_count'):
            self.assertIn(key, data)
