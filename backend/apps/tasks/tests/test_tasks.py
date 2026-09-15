"""Дерево задач, повторяющиеся задачи, API."""

from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Employee, User
from apps.tasks.models import Task, TaskStatus
from apps.tasks.recurrence import generate_due


class ProgressTestCase(TestCase):
    def test_leaf_without_children_follows_its_own_status(self):
        todo = Task.objects.create(title="Сделать")
        done = Task.objects.create(title="Сделано", status=TaskStatus.DONE)
        assert todo.progress == 0
        assert done.progress == 100

    def test_parent_progress_is_the_share_of_done_children(self):
        parent = Task.objects.create(title="Выезд в район")
        Task.objects.create(title="Объект 1", parent=parent, status=TaskStatus.DONE)
        Task.objects.create(title="Объект 2", parent=parent, status=TaskStatus.DONE)
        Task.objects.create(title="Объект 3", parent=parent, status=TaskStatus.TODO)
        assert parent.progress == 67

    def test_completed_at_is_set_and_cleared_with_status(self):
        task = Task.objects.create(title="Задача")
        assert task.completed_at is None

        task.status = TaskStatus.DONE
        task.save()
        assert task.completed_at is not None

        task.status = TaskStatus.TODO
        task.save()
        assert task.completed_at is None


class RecurrenceTestCase(TestCase):
    def setUp(self) -> None:
        self.employee = Employee.objects.create(tab_number="09", full_name="Метролог М. М.")

    def test_first_instance_uses_the_template_due_date_as_anchor(self):
        template = Task.objects.create(
            title="Поверка эталона", assignee=self.employee,
            due_date=dt.date(2026, 1, 1), recurrence="0 6 1 * *",  # раз в месяц, 1 числа
        )
        created = generate_due(today=dt.date(2026, 2, 1))
        assert len(created) == 1
        assert created[0].due_date == dt.date(2026, 2, 1)
        assert created[0].recurrence_parent_id == template.pk
        assert created[0].recurrence == ""  # экземпляр сам не повторяется

    def test_does_not_generate_before_the_next_occurrence(self):
        Task.objects.create(
            title="Поверка эталона", due_date=dt.date(2026, 1, 1), recurrence="0 6 1 * *",
        )
        assert generate_due(today=dt.date(2026, 1, 15)) == []

    def test_is_idempotent_once_the_instance_for_the_period_exists(self):
        Task.objects.create(title="Отчётность", due_date=dt.date(2026, 1, 1), recurrence="0 6 1 * *")
        first = generate_due(today=dt.date(2026, 2, 1))
        second = generate_due(today=dt.date(2026, 2, 1))
        assert len(first) == 1
        assert second == []

    def test_bad_cron_expression_does_not_break_the_whole_run(self):
        Task.objects.create(title="Сломанная", due_date=dt.date(2026, 1, 1), recurrence="не cron")
        good = Task.objects.create(title="Хорошая", due_date=dt.date(2026, 1, 1), recurrence="0 6 1 * *")
        created = generate_due(today=dt.date(2026, 2, 1))
        assert [task.recurrence_parent_id for task in created] == [good.pk]


class TaskApiTestCase(TestCase):
    def setUp(self) -> None:
        self.employee = Employee.objects.create(tab_number="03", full_name="Поверитель П. П.")
        self.user = User.objects.create_user("worker@ei.test", "pw")
        self.user.employee = self.employee
        self.employee.user = self.user
        self.employee.save()

        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_mine_filters_by_the_current_employee(self):
        Task.objects.create(title="Моя", assignee=self.employee)
        Task.objects.create(title="Чужая", assignee=None)

        response = self.api.get(reverse("task_list_create"), {"mine": 1})
        titles = [row["title"] for row in response.data["results"]]
        assert titles == ["Моя"]

    def test_board_groups_by_status(self):
        Task.objects.create(title="К выполнению", status=TaskStatus.TODO)
        Task.objects.create(title="Готово", status=TaskStatus.DONE)

        response = self.api.get(reverse("task_board"))
        assert [row["title"] for row in response.data["todo"]] == ["К выполнению"]
        assert [row["title"] for row in response.data["done"]] == ["Готово"]

    def test_workload_sums_estimated_hours_per_employee(self):
        today = timezone.localdate()
        Task.objects.create(title="A", assignee=self.employee, due_date=today, estimated_hours="2.5")
        Task.objects.create(title="B", assignee=self.employee, due_date=today, estimated_hours="1.5")

        response = self.api.get(
            reverse("task_workload"),
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        row = response.data["employees"][0]
        assert row["employee_id"] == self.employee.pk
        assert row["hours"] == 4.0
        assert row["count"] == 2
