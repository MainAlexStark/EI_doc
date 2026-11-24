import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notify", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountSecurity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("password_changed_at", models.DateTimeField(blank=True, null=True, verbose_name="пароль сменён")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="account_security", to=settings.AUTH_USER_MODEL, verbose_name="пользователь")),
            ],
            options={"verbose_name": "безопасность входа", "verbose_name_plural": "безопасность входа"},
        ),
    ]
