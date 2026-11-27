from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('juntagrico_pulse', '0003_copy_vegetable_coverage'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='surveyresult',
            name='consumption_level',
        ),
        migrations.RemoveField(
            model_name='surveyresult',
            name='subscription_percent',
        ),
    ]
