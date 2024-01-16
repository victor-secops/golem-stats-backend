# Generated by Django 4.1.7 on 2024-01-16 23:50

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api2', '0011_offer_times_more_expensive'),
    ]

    operations = [
        migrations.AddField(
            model_name='offer',
            name='cheaper_than',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cheaper_offers', to='api2.ec2instance'),
        ),
        migrations.AddField(
            model_name='offer',
            name='times_cheaper',
            field=models.FloatField(null=True),
        ),
    ]
