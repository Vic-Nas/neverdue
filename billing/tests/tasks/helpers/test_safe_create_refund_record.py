# billing/tests/tasks/helpers/test_safe_create_refund_record.py
from django.test import TestCase

from billing.models import RefundRecord
from billing.tasks import _safe_create_refund_record
from billing.tests.helpers import make_coupon, make_redemption, make_subscription, make_user


class SafeCreateRefundRecordTest(TestCase):

    def setUp(self):
        self.user = make_user('safeuser')
        make_subscription(self.user)
        self.coupon = make_coupon(code='SAFE01')
        self.redemption = make_redemption(self.coupon, self.user)
        self.kwargs = dict(
            redemption=self.redemption,
            stripe_invoice_id='in_safe1',
            stripe_refund_id='re_safe1',
            amount=100,
        )

    def test_first_call_creates_row(self):
        _safe_create_refund_record(self.kwargs)
        self.assertEqual(RefundRecord.objects.count(), 1)

    def test_second_call_swallows_integrity_error(self):
        _safe_create_refund_record(self.kwargs)
        _safe_create_refund_record(self.kwargs)  # duplicate — must not raise

    def test_count_stays_one_after_both_calls(self):
        _safe_create_refund_record(self.kwargs)
        _safe_create_refund_record(self.kwargs)
        self.assertEqual(RefundRecord.objects.count(), 1)
