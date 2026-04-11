# billing/migrations/0003_refactor_coupons.py
"""
Migration for the billing refactor:
  1. Create UserCoupon + RefundRecord.
  2. Data-migrate existing relationships:
     - referred_by → UserCoupon(percent=12.50, users=[user, referrer])
     - CouponRedemption → UserCoupon(percent=coupon.percent, users=[user, admin])
  3. Drop Coupon, CouponRedemption.
  4. Drop Subscription.referral_code_generated_at.

User.referred_by is dropped in accounts migration 0004.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_referrals_forward(apps, schema_editor):
    pass  # referred_by field no longer exists; data already migrated on prod


def migrate_redemptions_forward(apps, schema_editor):
    """
    Convert CouponRedemption rows to UserCoupon rows using the admin sentinel.
    If no admin user exists the redemptions are skipped (shouldn't happen in prod).
    """
    User = apps.get_model('accounts', 'User')
    CouponRedemption = apps.get_model('billing', 'CouponRedemption')
    UserCoupon = apps.get_model('billing', 'UserCoupon')

    try:
        admin = User.objects.get(username='admin')
    except User.DoesNotExist:
        return

    for redemption in CouponRedemption.objects.select_related('coupon', 'user'):
        coupon = UserCoupon.objects.create(percent=str(redemption.coupon.percent))
        coupon.users.set([redemption.user, admin])


def noop_reverse(apps, schema_editor):
    pass  # data migration is intentionally non-reversible


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_coupon_subscription_referral_code_and_more'),
        ('accounts', '0002_rename_revoke_to_save_gcal'),
    ]

    operations = [
        # 1. Create UserCoupon
        migrations.CreateModel(
            name='UserCoupon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('percent', models.DecimalField(max_digits=5, decimal_places=2)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('users', models.ManyToManyField(
                    related_name='coupons', to=settings.AUTH_USER_MODEL
                )),
            ],
        ),

        # 2. Create RefundRecord
        migrations.CreateModel(
            name='RefundRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('stripe_invoice_id', models.CharField(max_length=255)),
                ('stripe_refund_id', models.CharField(max_length=255)),
                ('amount', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user_coupon', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='refunds',
                    to='billing.usercoupon',
                )),
            ],
            options={
                'unique_together': {('user_coupon', 'stripe_invoice_id')},
            },
        ),

        # 3. Data migrations
        migrations.RunPython(migrate_referrals_forward, noop_reverse),
        migrations.RunPython(migrate_redemptions_forward, noop_reverse),

        # 4. Drop CouponRedemption (FK to Coupon, safe to drop first)
        migrations.DeleteModel(name='CouponRedemption'),

        # 5. Drop Coupon
        migrations.DeleteModel(name='Coupon'),

        # 6. Drop referral_code_generated_at
        migrations.RemoveField(model_name='subscription', name='referral_code_generated_at'),
    ]