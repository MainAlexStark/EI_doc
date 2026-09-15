from django.core.management.base import BaseCommand

from apps.tasks.recurrence import generate_due


class Command(BaseCommand):
    help = "Завести очередные экземпляры повторяющихся задач, чей срок наступил"

    def handle(self, *args, **options):
        created = generate_due()
        if not created:
            self.stdout.write("Новых задач нет")
            return
        for task in created:
            self.stdout.write(f"  #{task.id} {task.title} — {task.due_date}")
        self.stdout.write(self.style.SUCCESS(f"Создано задач: {len(created)}"))
