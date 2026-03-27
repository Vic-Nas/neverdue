# emails/migrations/0001_initial.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ScanJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[('queued', 'Queued'), ('processing', 'Processing'), ('done', 'Done'), ('failed', 'Failed')],
                    default='queued',
                    max_length=20,
                )),
                ('source', models.CharField(
                    choices=[('email', 'Email'), ('upload', 'Upload'), ('reprocess', 'Reprocess')],
                    default='email',
                    max_length=20,
                )),
                ('summary', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='scan_jobs',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
