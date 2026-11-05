"""Перенести личную память старого ассистента в агента на agentkit.

Общая память компании, правила руководства и файлы контекста остаются в EI_doc —
агент получает их от MCP-сервера EI_doc (apps/assistant/host.py). Переезжает только
личная память сотрудников: в агенте она своя у каждого и пополняется сама.

Повторный запуск безопасен: запись, которая у сотрудника в агенте уже есть, не дублируется.
"""

from __future__ import annotations

from urllib.parse import quote

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.assistant.models import MemoryItem, Scope


class Command(BaseCommand):
    help = "Перенести личную память ассистента в агента (AGENT_INTERNAL_URL, AGENT_SERVICE_TOKEN)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="только показать, что переедет")

    def handle(self, *args, dry_run=False, **opts):
        base, token = settings.AGENT_INTERNAL_URL.rstrip("/"), settings.AGENT_SERVICE_TOKEN
        if not base or not token:
            raise CommandError("Нужны AGENT_INTERNAL_URL и AGENT_SERVICE_TOKEN в .env (их пишет agent/deploy.sh)")
        items = MemoryItem.objects.filter(scope=Scope.PERSONAL).select_related("user").order_by("user_id", "pk")
        by_user: dict = {}
        for item in items:
            by_user.setdefault(item.user, []).append(item.text.strip())
        moved = skipped = 0
        for user, texts in by_user.items():
            headers = {"Authorization": f"Bearer {token}", "X-Agent-User-Id": quote(str(user.pk))}
            r = requests.get(f"{base}/api/memory", headers=headers, timeout=30)
            if r.status_code != 200:
                raise CommandError(f"Агент ответил {r.status_code}: {r.text[:300]}")
            have = {m.get("text", "").strip() for m in r.json() if m.get("scope") == f"user:{user.pk}"}
            for text in texts:
                if not text or text in have:
                    skipped += 1
                    continue
                if not dry_run:
                    r = requests.post(f"{base}/api/memory", json={"text": text, "scope": "user"},
                                      headers=headers, timeout=30)
                    if r.status_code != 200:
                        raise CommandError(f"Агент не сохранил запись: {r.status_code} {r.text[:300]}")
                have.add(text)
                moved += 1
            self.stdout.write(f"{user}: {len(texts)} записей")
        verb = "переедет" if dry_run else "перенесено"
        self.stdout.write(self.style.SUCCESS(f"Готово: {verb} {moved}, уже было {skipped}."))
