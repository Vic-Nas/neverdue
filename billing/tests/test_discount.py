# billing/tests/test_discount.py
"""
Discount computation tests (pure logic) + _push_combined_discount Stripe side-effects.
"""
from billing.discount import compute_discount, _count_active_referrals
from billing.models import Coupon, CouponRedemption, Subscription
from billing.tests.helpers import BillingTestCase, make_user, stripe_client
from billing.views.webhook import _push_combined_discount


class ComputeDiscountPure(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("discuser")

    def test_no_discount_returns_zero(self):
        self.assertEqual(compute_discount(self.user), 0)

    def test_single_coupon_discount(self):
        coupon = Coupon.objects.create(code="PURE-10", percent=10, label="10off")
        CouponRedemption.objects.create(user=self.user, coupon=coupon)
        self.assertEqual(compute_discount(self.user), 10)

    def test_multiple_coupons_stack(self):
        c1 = Coupon.objects.create(code="PURE-20", percent=20, label="20off")
        c2 = Coupon.objects.create(code="PURE-15", percent=15, label="15off")
        CouponRedemption.objects.create(user=self.user, coupon=c1)
        CouponRedemption.objects.create(user=self.user, coupon=c2)
        self.assertEqual(compute_discount(self.user), 35)

    def test_discount_capped_at_100(self):
        c1 = Coupon.objects.create(code="CAP-60", percent=60, label="60off")
        c2 = Coupon.objects.create(code="CAP-70", percent=70, label="70off")
        CouponRedemption.objects.create(user=self.user, coupon=c1)
        CouponRedemption.objects.create(user=self.user, coupon=c2)
        self.assertEqual(compute_discount(self.user), 100)

    def test_referral_count_excludes_trialing(self):
        referred = make_user("referred1")
        s = stripe_client()
        c = s.Customer.create(email=referred.email)
        self.track("customer", c.id)
        Subscription.objects.create(
            user=referred,
            stripe_customer_id=c.id,
            status="trialing",
        )
        referred.referred_by = self.user
        referred.save()
        self.assertEqual(_count_active_referrals(self.user), 0)

    def test_referral_count_includes_active(self):
        referred = make_user("referred2")
        s = stripe_client()
        c = s.Customer.create(email=referred.email)
        self.track("customer", c.id)
        Subscription.objects.create(
            user=referred,
            stripe_customer_id=c.id,
            status="active",
        )
        referred.referred_by = self.user
        referred.save()
        self.assertEqual(_count_active_referrals(self.user), 1)

    def test_referral_adds_12_5_percent(self):
        referred = make_user("referred3")
        s = stripe_client()
        c = s.Customer.create(email=referred.email)
        self.track("customer", c.id)
        Subscription.objects.create(
            user=referred,
            stripe_customer_id=c.id,
            status="active",
        )
        referred.referred_by = self.user
        referred.save()
        # 12.5 -> int() = 12
        self.assertEqual(compute_discount(self.user), 12)


class PushCombinedDiscountStripe(BillingTestCase):
    """
    _push_combined_discount makes Stripe API calls. We test:
    1. The coupon create/delete/modify sequence is correct.
    2. Zero-discount path sends discounts=[].
    We mock stripe calls to avoid needing a real subscription on Stripe test mode.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user("pushdiscuser")
        s = stripe_client()
        self.cust = s.Customer.create(email=self.user.email)
        self.track("customer", self.cust.id)
        self.sub_obj = Subscription.objects.create(
            user=self.user,
            stripe_customer_id=self.cust.id,
            stripe_subscription_id="sub_mockfake",
            status="active",
        )

    def test_zero_discount_does_not_create_coupon(self):
        from unittest.mock import patch, call
        with patch("stripe.Coupon.delete") as mock_del, \
             patch("stripe.Coupon.create") as mock_create, \
             patch("stripe.Subscription.modify") as mock_modify:
            _push_combined_discount(self.sub_obj)
            mock_create.assert_not_called()
            mock_modify.assert_called_once_with("sub_mockfake", discounts=[])

    def test_nonzero_discount_creates_auto_coupon_and_applies(self):
        from unittest.mock import patch, call
        coupon = Coupon.objects.create(code="PUSH-20", percent=20, label="Push 20")
        CouponRedemption.objects.create(user=self.user, coupon=coupon)
        auto_id = f"nvd-auto-{self.user.pk}"

        with patch("stripe.Coupon.delete") as mock_del, \
             patch("stripe.Coupon.create") as mock_create, \
             patch("stripe.Subscription.modify") as mock_modify:
            _push_combined_discount(self.sub_obj)
            mock_create.assert_called_once_with(
                id=auto_id, percent_off=20, duration="once"
            )
            mock_modify.assert_called_once_with(
                "sub_mockfake", discounts=[{"coupon": auto_id}]
            )

    def test_auto_coupon_delete_attempted_before_recreate(self):
        """Old nvd-auto-<pk> coupon is always deleted before recreating."""
        from unittest.mock import patch, MagicMock
        coupon = Coupon.objects.create(code="PUSH-10", percent=10, label="Push 10")
        CouponRedemption.objects.create(user=self.user, coupon=coupon)

        call_order = []
        with patch("stripe.Coupon.delete", side_effect=lambda id: call_order.append(f"del:{id}")) as _, \
             patch("stripe.Coupon.create", side_effect=lambda **kw: call_order.append(f"create:{kw['id']}")) as _, \
             patch("stripe.Subscription.modify"):
            _push_combined_discount(self.sub_obj)

        auto_id = f"nvd-auto-{self.user.pk}"
        self.assertEqual(call_order[0], f"del:{auto_id}")
        self.assertEqual(call_order[1], f"create:{auto_id}")
