# Generated by Django 4.1.7 on 2024-01-14 13:23

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('api2', '0005_uptimeevent_nodeuptime'),
    ]

    operations = [
        migrations.CreateModel(
            name='HealtcheckTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(max_length=42)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='api2.node')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RemoveField(
            model_name='uptimeevent',
            name='node',
        ),
        migrations.DeleteModel(
            name='NodeUptime',
        ),
        migrations.DeleteModel(
            name='UptimeEvent',
        ),
    ]
