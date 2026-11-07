import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("logistics", "0001_trips")]

    operations = [
        migrations.AddField(
            model_name="tripstop",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, db_index=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
