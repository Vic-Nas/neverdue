# accounts/migrations/0004_remove_user_referred_by.py
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_referred_by'),
        # Must run after billing data migration has already converted
        # referred_by rows to UserCoupon rows.
        ('billing', '0003_refactor_coupons'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='referred_by',
        ),
    ]
