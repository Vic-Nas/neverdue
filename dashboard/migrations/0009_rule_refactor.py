from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0008_event_scan_job'),
    ]

    operations = [
        # Add rule_type field
        migrations.AddField(
            model_name='rule',
            name='rule_type',
            field=models.CharField(
                choices=[('sender', 'Sender'), ('keyword', 'Keyword'), ('prompt', 'Prompt injection')],
                default='keyword',
                max_length=20,
            ),
        ),
        # Add prompt_text field
        migrations.AddField(
            model_name='rule',
            name='prompt_text',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        # Allow pattern to be blank
        migrations.AlterField(
            model_name='rule',
            name='pattern',
            field=models.CharField(blank=True, max_length=255),
        ),
        # Update action choices and allow blank
        migrations.AlterField(
            model_name='rule',
            name='action',
            field=models.CharField(
                choices=[('categorize', 'Categorize'), ('discard', 'Discard')],
                blank=True,
                max_length=20,
            ),
        ),
        # Update category FK to use SET_NULL
        migrations.AlterField(
            model_name='rule',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='rules',
                to='dashboard.category',
            ),
        ),
        # Remove old unique_together constraint
        migrations.AlterUniqueTogether(
            name='rule',
            unique_together=set(),
        ),
    ]
