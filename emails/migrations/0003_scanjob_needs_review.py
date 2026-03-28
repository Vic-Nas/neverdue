# emails/migrations/0003_scanjob_needs_review.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('emails', '0002_rename_summary_scanjob_from_address_scanjob_notes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='scanjob',
            name='status',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('queued', 'Queued'),
                    ('processing', 'Processing'),
                    ('needs_review', 'Needs review'),
                    ('done', 'Done'),
                    ('failed', 'Failed'),
                ],
                default='queued',
            ),
        ),
        migrations.AlterField(
            model_name='scanjob',
            name='source',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('email', 'Email'),
                    ('upload', 'Upload'),
                ],
                default='email',
            ),
        ),
    ]
