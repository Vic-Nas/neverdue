from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0007_category_gcal_color_id'),
        ('emails', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='scan_job',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='events',
                to='emails.scanjob',
            ),
        ),
    ]
