from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0004_template_workspace"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="reasoning",
            field=models.JSONField(
                blank=True, default=dict,
                help_text="Рассуждения модели: details (reasoning_details OpenRouter, как пришли) и text. "
                          "Возвращаются модели в пределах текущего хода",
            ),
        ),
    ]
