# dashboard/migrations/0002_event_reminders.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='reminders',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
