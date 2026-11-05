from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assistant", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="external_id",
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
    ]
