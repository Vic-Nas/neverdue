# billing/tasks.py
"""
Procrastinate tasks for billing.
"""
import logging
import math
from datetime import timezone as dt_timezone

import stripe
from stripe import StripeError
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from procrastinate.contrib.django import app

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


@app.periodic(cron='0 6 1 * *')
@app.task(queue='billing')
def process_monthly_refunds(timestamp: int) -> None:
    """
    Runs on the 1st of each month at 06:00 UTC.

    For each UserCoupon, for each user on it:
      1. Find that user's paid invoice for the previous calendar month
         in the dj-stripe local Invoice table.
      2. Skip if: no paid invoice, user sub cancelled, invoice date is before
         the coupon's created_at, or a RefundRecord already exists for
         (coupon, invoice).
      3. Verify all other users on the coupon also paid that month.
         If any did not: skip everyone on this coupon for this month.
      4. Compute refund = invoice.amount_paid * (percent / 100), in cents.
      5. Create the Stripe Refund.
      6. Write a RefundRecord atomically (unique_together makes it idempotent).

    Admin sentinel (username='admin') never has an invoice and is never
    considered when checking whether partners paid.

    Each UserCoupon is processed independently — one failure does not block
    others. Exceptions are logged and re-raised so Procrastinate retries
    the individual coupon's work on the next attempt. RefundRecord
    unique_together ensures the whole job is safe to re-run.
    """
    from billing.models import RefundRecord, UserCoupon
    import djstripe.models as djstripe

    now = timezone.now()
    # Previous calendar month
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_of_this_month
    last_month_start = (first_of_this_month.replace(day=1) - timezone.timedelta(days=1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    try:
        admin = __import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model()
        admin_user = admin.objects.get(username='admin')
        admin_pk = admin_user.pk
    except Exception:
        admin_pk = None

    def _get_paid_invoice(user):
        """
        Return the dj-stripe Invoice for this user that was paid last month,
        or None.
        """
        sub_obj = getattr(user, 'subscription', None)
        if not sub_obj or not sub_obj.stripe_customer_id:
            return None
        last_month_start_ts = int(last_month_start.timestamp())
        last_month_end_ts = int(last_month_end.timestamp())
        return (
            djstripe.Invoice.objects
            .filter(
                customer__id=sub_obj.stripe_customer_id,
                stripe_data__status='paid',
                stripe_data__period_start__gte=last_month_start_ts,
                stripe_data__period_start__lt=last_month_end_ts,
            )
            .order_by('-stripe_data__period_start')
            .first()
        )

    for coupon in UserCoupon.objects.prefetch_related('users__subscription'):
        users = list(coupon.users.all())
        # Filter out admin sentinel — they never pay and never receive refunds
        paying_users = [u for u in users if u.pk != admin_pk]

        if not paying_users:
            continue

        # --- Step 1: Collect invoices for all paying users on this coupon ---
        invoices = {}
        for u in paying_users:
            inv = _get_paid_invoice(u)
            invoices[u.pk] = inv

        # --- Step 2: Check all paying users paid last month ---
        all_paid = all(inv is not None for inv in invoices.values())
        if not all_paid:
            unpaid = [u.username for u in paying_users if invoices[u.pk] is None]
            logger.info(
                'process_monthly_refunds: coupon=%s skipped — unpaid users: %s',
                coupon.pk, unpaid,
            )
            continue

        # --- Step 3: Issue refunds ---
        coupon_failed = False
        for u in paying_users:
            inv = invoices[u.pk]

            # Skip if invoice pre-dates coupon creation
            period_start_ts = inv.period_start  # raw int from stripe_data
            period_start = timezone.datetime.fromtimestamp(period_start_ts, tz=dt_timezone.utc)
            if period_start < coupon.created_at:
                logger.info(
                    'process_monthly_refunds: coupon=%s user=%s skipped — '
                    'invoice pre-dates coupon', coupon.pk, u.pk,
                )
                continue

            # Skip if RefundRecord already exists (idempotency)
            if RefundRecord.objects.filter(
                user_coupon=coupon,
                stripe_invoice_id=inv.id,
            ).exists():
                logger.debug(
                    'process_monthly_refunds: coupon=%s user=%s invoice=%s already refunded',
                    coupon.pk, u.pk, inv.id,
                )
                continue

            refund_cents = math.ceil(inv.stripe_data['amount_paid'] * float(coupon.percent) / 100)
            if refund_cents <= 0:
                continue

            charge_id = inv.stripe_data.get('charge')
            if not charge_id:
                logger.warning(
                    'process_monthly_refunds: coupon=%s user=%s invoice=%s has no charge — skip',
                    coupon.pk, u.pk, inv.id,
                )
                continue

            try:
                refund = stripe.Refund.create(
                    charge=charge_id,
                    amount=refund_cents,
                )
            except StripeError:
                logger.exception(
                    'process_monthly_refunds: Stripe refund failed | '
                    'coupon=%s user=%s invoice=%s', coupon.pk, u.pk, inv.id,
                )
                coupon_failed = True
                break  # do not write a partial RefundRecord; Procrastinate will retry

            try:
                with transaction.atomic():
                    RefundRecord.objects.create(
                        user_coupon=coupon,
                        stripe_invoice_id=inv.id,
                        stripe_refund_id=refund.id,
                        amount=refund_cents,
                    )
            except IntegrityError:
                # Race between parallel retries — safe to ignore
                logger.info(
                    'process_monthly_refunds: duplicate RefundRecord skipped | '
                    'coupon=%s invoice=%s', coupon.pk, inv.id,
                )

            logger.info(
                'process_monthly_refunds: refunded %d cents | '
                'coupon=%s user=%s invoice=%s refund=%s',
                refund_cents, coupon.pk, u.pk, inv.id, refund.id,
            )

        if coupon_failed:
            raise RuntimeError(
                f'process_monthly_refunds: coupon={coupon.pk} had a Stripe error; '
                'Procrastinate will retry.'
            )