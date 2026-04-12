# billing/tasks.py
"""
Procrastinate tasks for billing.
"""
import logging
import math
from datetime import timezone as dt_timezone

import stripe
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from procrastinate.contrib.django import app

logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prev_month_window(now):
    """Return (start, end) aware datetimes bracketing the previous calendar month."""
    first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day_prev = first_of_this - timezone.timedelta(days=1)
    start = last_day_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, first_of_this


def _get_paid_invoice(user, month_start, month_end):
    """
    Return the dj-stripe Invoice that user paid last month, or None.
    Looks up via the local djstripe Invoice table (no Stripe API call).
    """
    import djstripe.models as djstripe

    sub_obj = getattr(user, 'subscription', None)
    if not sub_obj or not sub_obj.stripe_customer_id:
        return None

    start_ts = int(month_start.timestamp())
    end_ts = int(month_end.timestamp())
    return (
        djstripe.Invoice.objects
        .filter(
            customer__id=sub_obj.stripe_customer_id,
            stripe_data__status='paid',
            stripe_data__period_start__gte=start_ts,
            stripe_data__period_start__lt=end_ts,
        )
        .order_by('-stripe_data__period_start')
        .first()
    )


def _issue_refund(invoice, percent, label):
    """
    Issue a Stripe refund for percent% of invoice.amount_paid.
    Returns (stripe_refund_id, amount_cents) or raises StripeError.
    """
    amount_cents = math.ceil(invoice.stripe_data['amount_paid'] * percent / 100)
    if amount_cents <= 0:
        return None, 0

    charge_id = invoice.stripe_data.get('charge')
    if not charge_id:
        logger.warning('_issue_refund: no charge on invoice=%s (%s)', invoice.id, label)
        return None, 0

    refund = stripe.Refund.create(charge=charge_id, amount=amount_cents)
    return refund.id, amount_cents


def _safe_create_refund_record(model_kwargs):
    """Write a RefundRecord atomically; swallow IntegrityError on race retry."""
    from billing.models import RefundRecord
    try:
        with transaction.atomic():
            RefundRecord.objects.create(**model_kwargs)
    except IntegrityError:
        logger.info('_safe_create_refund_record: duplicate skipped | %s', model_kwargs)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@app.periodic(cron='0 6 1 * *')
@app.task(queue='billing')
def process_monthly_refunds(timestamp: int) -> None:
    """
    Runs on the 1st of each month at 06:00 UTC.

    Eligibility is simple: did the user pay during the previous calendar month?
    _get_paid_invoice enforces the billing window — no additional date checks
    against redeemed_at are needed.

    For each CouponRedemption:
      - Redeemer receives coupon.percent refund if head paid (or head=None).

    For each Coupon where head is set:
      - Head receives coupon.percent * count(redeemers who paid) refund,
        capped at 100% of their own invoice.

    All refund records are idempotent via RefundRecord unique constraints.
    A failure on one coupon does not block others.
    """
    from billing.models import Coupon, CouponRedemption, RefundRecord

    now = timezone.now()
    month_start, month_end = _prev_month_window(now)

    # -----------------------------------------------------------------------
    # 1. Redeemer refunds
    # -----------------------------------------------------------------------
    for redemption in CouponRedemption.objects.select_related(
        'coupon__head__subscription', 'user__subscription'
    ):
        coupon = redemption.coupon
        user = redemption.user
        label = f'redemption={redemption.pk}'

        # Skip future-dated redemptions (clock skew guard)
        if redemption.redeemed_at > now:
            logger.info(
                'process_monthly_refunds: %s skipped — redemption is future-dated', label,
            )
            continue

        # Head must have paid (or be None — NeverDue grant)
        head = coupon.head
        if head is not None:
            head_invoice = _get_paid_invoice(head, month_start, month_end)
            if head_invoice is None:
                logger.info(
                    'process_monthly_refunds: %s skipped — head=%s did not pay',
                    label, head.pk,
                )
                continue

        # Redeemer must have paid
        inv = _get_paid_invoice(user, month_start, month_end)
        if inv is None:
            logger.info(
                'process_monthly_refunds: %s skipped — redeemer=%s did not pay',
                label, user.pk,
            )
            continue

        # Idempotency check
        if RefundRecord.objects.filter(
            redemption=redemption, stripe_invoice_id=inv.id
        ).exists():
            continue

        try:
            refund_id, amount = _issue_refund(inv, float(coupon.percent), label)
        except stripe.error.StripeError:
            logger.exception('process_monthly_refunds: Stripe error | %s', label)
            raise RuntimeError(f'Stripe error on {label}')

        if refund_id:
            _safe_create_refund_record(dict(
                redemption=redemption,
                stripe_invoice_id=inv.id,
                stripe_refund_id=refund_id,
                amount=amount,
            ))
            logger.info(
                'process_monthly_refunds: redeemer refund %d cents | %s refund=%s',
                amount, label, refund_id,
            )

    # -----------------------------------------------------------------------
    # 2. Head refunds
    # -----------------------------------------------------------------------
    for coupon in Coupon.objects.filter(head__isnull=False).prefetch_related(
        'redemptions__user__subscription'
    ):
        head = coupon.head
        label = f'coupon={coupon.pk} head={head.pk}'

        head_inv = _get_paid_invoice(head, month_start, month_end)
        if head_inv is None:
            logger.info(
                'process_monthly_refunds: head refund skipped — head did not pay | %s', label,
            )
            continue

        # Count redeemers who paid this month (exclude future-dated redemptions)
        paid_redeemer_count = 0
        for redemption in coupon.redemptions.all():
            if redemption.redeemed_at > now:
                continue
            if _get_paid_invoice(redemption.user, month_start, month_end) is not None:
                paid_redeemer_count += 1

        if paid_redeemer_count == 0:
            continue

        effective_percent = min(float(coupon.percent) * paid_redeemer_count, 100.0)

        # Idempotency check
        if RefundRecord.objects.filter(
            coupon_head=coupon, stripe_invoice_id=head_inv.id
        ).exists():
            continue

        try:
            refund_id, amount = _issue_refund(head_inv, effective_percent, label)
        except stripe.error.StripeError:
            logger.exception('process_monthly_refunds: Stripe error | %s', label)
            raise RuntimeError(f'Stripe error on {label}')

        if refund_id:
            _safe_create_refund_record(dict(
                coupon_head=coupon,
                stripe_invoice_id=head_inv.id,
                stripe_refund_id=refund_id,
                amount=amount,
            ))
            logger.info(
                'process_monthly_refunds: head refund %d cents (%d redeemers) | %s refund=%s',
                amount, paid_redeemer_count, label, refund_id,
            )