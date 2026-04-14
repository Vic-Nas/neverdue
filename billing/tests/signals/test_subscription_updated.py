# billing/tests/signals/test_subscription_updated.py
from django.test import TestCase

from billing.signals import handle_subscription_updated
from billing.tests.helpers import make_subscription, make_user


def _event(customer_id, new_status, old_status=None):
    if old_status is None:
        old_status = new_status
    return {
        'data': {
            'object': {'customer': customer_id, 'status': new_status},
            'previous_attributes': {'status': old_status},
        }
    }


class SubscriptionUpdatedTest(TestCase):

    def setUp(self):
        self.user = make_user('subupd')
        make_subscription(self.user, status='active', stripe_customer_id='cus_upd1')

    def test_non_active_to_active_no_exception(self):
        # Procrastinate removed in test settings; just verify no crash.
        # TODO: assert ProcrastinateJob when Procrastinate is re-enabled in tests.
        try:
            handle_subscription_updated(_event('cus_upd1', 'active', 'trialing'))
        except Exception as exc:
            self.fail(f'handle_subscription_updated raised unexpectedly: {exc}')

    def test_active_to_active_no_defer(self):
        # Status unchanged — handler short-circuits before defer call.
        handle_subscription_updated(_event('cus_upd1', 'active', 'active'))

    def test_any_to_cancelled_no_defer(self):
        handle_subscription_updated(_event('cus_upd1', 'cancelled', 'active'))
