"""Заготовка программы для фармацевтических холодильников — по образцу протокола 1886/2026."""

import json
from pathlib import Path

from django.db import migrations

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "programs.json"


def seed(apps, schema_editor):
    Program = apps.get_model("equipment", "AttestationProgram")
    for item in json.loads(FIXTURE.read_text(encoding="utf-8")):
        Program.objects.get_or_create(title=item["title"], defaults={**item, "needs_review": True})


class Migration(migrations.Migration):
    dependencies = [("equipment", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
