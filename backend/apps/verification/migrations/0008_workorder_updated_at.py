import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("verification", "0007_personal_data")]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, db_index=True, default=django.utils.timezone.now,
                                       verbose_name="изменён"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="historicalworkorder",
            name="updated_at",
            field=models.DateTimeField(blank=True, db_index=True, default=django.utils.timezone.now,
                                       editable=False, verbose_name="изменён"),
            preserve_default=False,
        ),
    ]
