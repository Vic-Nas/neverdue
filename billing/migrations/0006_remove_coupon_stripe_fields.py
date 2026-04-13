from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_remove_usercoupon_users_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='coupon',
            name='stripe_coupon_id',
        ),
        migrations.RemoveField(
            model_name='coupon',
            name='stripe_promotion_code_id',
        ),
    ]
