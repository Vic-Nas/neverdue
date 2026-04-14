# billing/tests/models/test_refund_record.py
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from billing.models import CouponRedemption, RefundRecord
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


def _rr(**kwargs):
    defaults = dict(stripe_refund_id='re_test', amount=100, stripe_invoice_id='in_test')
    defaults.update(kwargs)
    return RefundRecord.objects.create(**defaults)


class RefundRecordModelTest(TestCase):

    def setUp(self):
        self.head = make_user('head1')
        self.redeemer = make_user('redeemer1')
        make_subscription(self.head)
        make_subscription(self.redeemer)
        self.coupon = make_coupon(head=self.head, code='REC001')
        self.redemption = make_redemption(self.coupon, self.redeemer)

    def test_create_with_redemption(self):
        rr = _rr(redemption=self.redemption, stripe_invoice_id='in_1')
        self.assertEqual(rr.redemption, self.redemption)
        self.assertIsNone(rr.coupon_head)

    def test_create_with_coupon_head(self):
        rr = _rr(coupon_head=self.coupon, stripe_invoice_id='in_2')
        self.assertEqual(rr.coupon_head, self.coupon)
        self.assertIsNone(rr.redemption)

    def test_unique_redemption_invoice(self):
        _rr(redemption=self.redemption, stripe_invoice_id='in_dup')
        with self.assertRaises(IntegrityError):
            _rr(redemption=self.redemption, stripe_invoice_id='in_dup')

    def test_unique_head_invoice(self):
        _rr(coupon_head=self.coupon, stripe_invoice_id='in_dup_h')
        with self.assertRaises(IntegrityError):
            _rr(coupon_head=self.coupon, stripe_invoice_id='in_dup_h')

    def test_same_invoice_id_on_both_types_allowed(self):
        _rr(redemption=self.redemption, stripe_invoice_id='in_shared')
        _rr(coupon_head=self.coupon, stripe_invoice_id='in_shared')
        self.assertEqual(RefundRecord.objects.filter(stripe_invoice_id='in_shared').count(), 2)

    def test_on_delete_protect(self):
        _rr(redemption=self.redemption, stripe_invoice_id='in_prot')
        with self.assertRaises(ProtectedError):
            self.redemption.delete()

    def test_str_variants(self):
        rr_r = _rr(redemption=self.redemption, stripe_invoice_id='in_str1')
        rr_h = _rr(coupon_head=self.coupon, stripe_invoice_id='in_str2')
        self.assertIn('redemption=', str(rr_r))
        self.assertIn('head_coupon=', str(rr_h))
