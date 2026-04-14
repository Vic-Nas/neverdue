# billing/tests/signals/test_subscription_updated.py
from unittest.mock import patch

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
        # Procrastinate is removed in test settings; mock defer so the handler
        # can be tested without a live Procrastinate app.
        with patch('billing.signals.retry_jobs_after_plan_upgrade.defer') as mock_defer:
            handle_subscription_updated(_event('cus_upd1', 'active', 'trialing'))
            mock_defer.assert_called_once_with(user_id=self.user.pk)

    def test_active_to_active_no_defer(self):
        # Status unchanged — handler short-circuits before defer call.
        with patch('billing.signals.retry_jobs_after_plan_upgrade.defer') as mock_defer:
            handle_subscription_updated(_event('cus_upd1', 'active', 'active'))
            mock_defer.assert_not_called()

    def test_any_to_cancelled_no_defer(self):
        with patch('billing.signals.retry_jobs_after_plan_upgrade.defer') as mock_defer:
            handle_subscription_updated(_event('cus_upd1', 'cancelled', 'active'))
            mock_defer.assert_not_called()
