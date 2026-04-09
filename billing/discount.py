# billing/discount.py
"""
Pure discount computation — no side effects, no Stripe calls.

Usage:
    from billing.discount import compute_discount
    pct = compute_discount(user)  # int 0–100
"""
from django.utils import timezone

REFERRAL_PERCENT = 12  # 12.5 rounds to 12 per referral; handled as float below
REFERRAL_PERCENT_FLOAT = 12.5


def compute_discount(user):
    """
    Return the combined discount percent (0–100) for a user, based on:
      - Active CouponRedemptions
      - Active referred subscribers (excluding those still in trial)

    This is computed live and reflects the current state — what will be
    applied at the user's next billing cycle.
    """
    from billing.models import CouponRedemption

    total = 0.0

    # Sum all active coupon redemptions (redemptions persist even after coupon expires)
    redemptions = (
        CouponRedemption.objects
        .filter(user=user)
        .select_related('coupon')
    )
    for r in redemptions:
        total += r.coupon.percent

    # Count active paid referred users (exclude trialing — they haven't paid yet)
    referral_count = _count_active_referrals(user)
    total += referral_count * REFERRAL_PERCENT_FLOAT

    return min(int(total), 100)


def _count_active_referrals(user):
    """
    Count users referred by this user who currently have an active (non-trialing)
    paid subscription.
    """
    from accounts.models import User as UserModel
    return (
        UserModel.objects
        .filter(
            referred_by=user,
            subscription__status='active',
        )
        .count()
    )


def referral_summary(user):
    """
    Return a list of dicts describing referred users, for display on billing page.
    Each dict: {masked_email, status, counts_toward_discount}
    """
    from accounts.models import User as UserModel
    referred = (
        UserModel.objects
        .filter(referred_by=user)
        .select_related('subscription')
        .order_by('date_joined')
    )
    result = []
    for u in referred:
        sub = getattr(u, 'subscription', None)
        status = sub.status if sub else 'no subscription'
        counts = sub is not None and sub.status == 'active'
        result.append({
            'masked_email': _mask_email(u.email),
            'status': status,
            'counts': counts,
        })
    return result


def _mask_email(email):
    """jo***e@gm***.com"""
    local, _, domain = email.partition('@')
    domain_name, dot, tld = domain.rpartition('.')
    masked_local = local[:2] + '***' + local[-1:] if len(local) > 3 else local[:1] + '***'
    masked_domain = domain_name[:2] + '***' if len(domain_name) > 2 else domain_name
    return f'{masked_local}@{masked_domain}.{tld}'
