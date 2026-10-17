"""Личный кабинет у ассистента: график, наряды, задачи, заявки — через карточку подтверждения."""

from __future__ import annotations

import datetime as dt
import json
from unittest import mock

from django.test import TestCase

from apps.assistant import actions, cabinet
from apps.assistant.models import Conversation, PendingAction
from apps.assistant.tools import ToolContext, ToolFailure, all_tools
from apps.core.models import Employee, Role, User
from apps.hub.models import EmployeeAvailability, Request, RequestStatus
from apps.tasks.models import Task, TaskStatus
from apps.verification.models import Client, Site, WorkOrder, WorkOrderStatus

TODAY = dt.date(2026, 10, 19)  # понедельник


class Base(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.me = Employee.objects.create(tab_number="05", full_name="Стариков Алексей", user=self.user)
        self.other_user = User.objects.create_user("o@ei.test", "pw", role=Role.VERIFIER)
        self.other = Employee.objects.create(tab_number="06", full_name="Иванов Иван", user=self.other_user)
        self.boss = User.objects.create_user("h@ei.test", "pw", role=Role.HEAD)
        self.observer = User.objects.create_user("x@ei.test", "pw", role=Role.OBSERVER)
        self.conv = Conversation.objects.create(user=self.user)

    def ctx(self, user=None) -> ToolContext:
        user = user or self.user
        conv = self.conv if user == self.user else Conversation.objects.create(user=user)
        return ToolContext(user=user, conversation=conv)

    def confirm(self, result, user=None) -> PendingAction:
        action = PendingAction.objects.get(pk=result.ui["action"])
        return actions.decide(action, user or action.conversation.user, confirm=True)


class RegistrationTests(Base):
    def test_tools_are_registered(self):
        tools, _ = all_tools(with_mcp=False)
        for name in ("ei_me", "ei_availability_propose", "ei_work_order_propose", "ei_task_propose",
                     "ei_request_propose"):
            self.assertIn(name, tools)


class AvailabilityTests(Base):
    def test_every_day_14_16_until_sunday(self):
        res = cabinet.ei_availability_propose(self.ctx(), {
            "add": [{"date_from": "2026-10-19", "date_to": "2026-10-25", "start_time": "14", "end_time": "16:00"}],
        })
        self.assertEqual(EmployeeAvailability.objects.count(), 0)  # до подтверждения ничего не записано
        action = PendingAction.objects.get(pk=res.ui["action"])
        self.assertEqual(len(action.payload["create"]), 7)
        self.assertIn("19.10–25.10", json.dumps(action.payload["view"], ensure_ascii=False))
        self.assertEqual(self.confirm(res).status, PendingAction.Status.DONE)
        slots = EmployeeAvailability.objects.filter(employee=self.me)
        self.assertEqual(slots.count(), 7)
        self.assertEqual({(s.start_time, s.end_time) for s in slots}, {(dt.time(14), dt.time(16))})

    def test_weekdays_and_duplicates_skipped(self):
        EmployeeAvailability.objects.create(employee=self.me, date=TODAY, start_time=dt.time(14), end_time=dt.time(16))
        res = cabinet.ei_availability_propose(self.ctx(), {
            "add": [{"date_from": "2026-10-19", "date_to": "2026-10-25", "weekdays": [1, 2, 3, 4, 5],
                     "start_time": "14:00", "end_time": "16:00"}],
        })
        self.assertIn("пропущены", res.content)
        self.confirm(res)
        self.assertEqual(EmployeeAvailability.objects.filter(employee=self.me).count(), 5)

    def test_replace_existing(self):
        old = EmployeeAvailability.objects.create(employee=self.me, date=TODAY, start_time=dt.time(9), end_time=dt.time(12))
        res = cabinet.ei_availability_propose(self.ctx(), {
            "add": [{"dates": ["2026-10-19"], "start_time": "14:00", "end_time": "16:00"}], "replace_existing": True,
        })
        self.confirm(res)
        self.assertFalse(EmployeeAvailability.objects.filter(pk=old.pk).exists())
        self.assertEqual(EmployeeAvailability.objects.get(employee=self.me).start_time, dt.time(14))

    def test_remove_range(self):
        for i in range(3):
            EmployeeAvailability.objects.create(employee=self.me, date=TODAY + dt.timedelta(days=i))
        res = cabinet.ei_availability_propose(self.ctx(), {"remove": {"date_from": "2026-10-20", "date_to": "2026-10-21"}})
        self.confirm(res)
        self.assertEqual(list(EmployeeAvailability.objects.values_list("date", flat=True)), [TODAY])

    def test_past_and_bad_time_rejected(self):
        with self.assertRaises(ToolFailure):
            cabinet.ei_availability_propose(self.ctx(), {"add": [{"dates": ["2026-10-01"]}]})
        with self.assertRaises(ToolFailure):
            cabinet.ei_availability_propose(self.ctx(), {"add": [{"dates": ["2026-10-20"], "start_time": "16", "end_time": "14"}]})

    def test_foreign_schedule_only_for_managers(self):
        with self.assertRaises(ToolFailure):
            cabinet.ei_availability_propose(self.ctx(), {"employee_id": self.other.pk, "add": [{"dates": ["2026-10-20"]}]})
        res = cabinet.ei_availability_propose(self.ctx(self.boss), {"employee_id": self.other.pk,
                                                                    "add": [{"dates": ["2026-10-20"]}]})
        self.confirm(res)
        self.assertEqual(EmployeeAvailability.objects.get().employee, self.other)

    def test_observer_cannot_write(self):
        Employee.objects.create(tab_number="07", full_name="Наблюдатель", user=self.observer)
        with self.assertRaises(ToolFailure):
            cabinet.ei_availability_propose(self.ctx(self.observer), {"add": [{"dates": ["2026-10-20"]}]})


class WorkOrderTests(Base):
    def setUp(self) -> None:
        super().setUp()
        client = Client.objects.create(name="Петров П.")
        site = Site.objects.create(client=client, address="Киров, ул. Ленина, 1")
        self.wo = WorkOrder.objects.create(client=client, site=site, assigned_employee=self.me, scheduled_date=TODAY)

    def test_list_mine(self):
        rows = json.loads(cabinet.ei_work_orders(self.ctx(), {}).content)
        self.assertEqual([r["id"] for r in rows], [self.wo.pk])
        self.assertEqual(cabinet.ei_work_orders(self.ctx(self.other_user), {}).content, "Нарядов не найдено.")

    def test_reschedule(self):
        res = cabinet.ei_work_order_propose(self.ctx(), {"work_order_id": self.wo.pk, "scheduled_date": "2026-10-21",
                                                         "scheduled_time": "10:30"})
        self.confirm(res)
        self.wo.refresh_from_db()
        self.assertEqual((self.wo.scheduled_date, self.wo.scheduled_time), (dt.date(2026, 10, 21), dt.time(10, 30)))

    def test_cannot_close_manually_or_touch_foreign(self):
        with self.assertRaises(ToolFailure):
            cabinet.ei_work_order_propose(self.ctx(), {"work_order_id": self.wo.pk, "status": "done"})
        with self.assertRaises(ToolFailure):
            cabinet.ei_work_order_propose(self.ctx(self.other_user), {"work_order_id": self.wo.pk, "note": "x"})

    def test_cancel_and_reassign_by_boss(self):
        res = cabinet.ei_work_order_propose(self.ctx(), {"work_order_id": self.wo.pk, "status": "cancelled"})
        self.confirm(res)
        self.wo.refresh_from_db()
        self.assertEqual(self.wo.status, WorkOrderStatus.CANCELLED)
        with self.assertRaises(ToolFailure):
            cabinet.ei_work_order_propose(self.ctx(), {"work_order_id": self.wo.pk,
                                                       "assigned_employee_id": self.other.pk})
        res = cabinet.ei_work_order_propose(self.ctx(self.boss), {"work_order_id": self.wo.pk, "status": "planned",
                                                                  "assigned_employee_id": self.other.pk})
        self.confirm(res)
        self.wo.refresh_from_db()
        self.assertEqual((self.wo.status, self.wo.assigned_employee), (WorkOrderStatus.PLANNED, self.other))


class TaskTests(Base):
    def test_create_several_and_update(self):
        res = cabinet.ei_task_propose(self.ctx(), {"items": [
            {"title": "Сдать отчёт", "due_date": "2026-10-23", "priority": "high"},
            {"title": "Поверить гири", "assignee_id": self.other.pk, "estimated_hours": 2},
        ]})
        self.confirm(res)
        mine = Task.objects.get(title="Сдать отчёт")
        self.assertEqual((mine.assignee, mine.created_by, mine.due_date), (self.me, self.me, dt.date(2026, 10, 23)))
        self.assertEqual(Task.objects.get(title="Поверить гири").assignee, self.other)
        rows = json.loads(cabinet.ei_tasks(self.ctx(), {}).content)
        self.assertEqual([r["title"] for r in rows], ["Сдать отчёт"])

        res = cabinet.ei_task_propose(self.ctx(), {"items": [{"op": "update", "task_id": mine.pk, "status": "done"}]})
        self.confirm(res)
        mine.refresh_from_db()
        self.assertEqual(mine.status, TaskStatus.DONE)
        self.assertIsNotNone(mine.completed_at)

    def test_foreign_task_and_bad_cron(self):
        foreign = Task.objects.create(title="Чужая", assignee=self.other, created_by=self.other)
        with self.assertRaises(ToolFailure):
            cabinet.ei_task_propose(self.ctx(), {"items": [{"task_id": foreign.pk, "status": "done"}]})
        with self.assertRaises(ToolFailure):
            cabinet.ei_task_propose(self.ctx(), {"items": [{"title": "x", "recurrence": "каждый день"}]})


class RequestTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.req = Request.objects.create(contact_name="Сидоров", contact_phone="+7 (900) 123-45-67",
                                          address="Киров, ул. Мира, 5", desired_date=dt.date(2026, 10, 22),
                                          status=RequestStatus.ROUTED, suggested_employee=self.me)

    def test_only_dispatcher(self):
        with self.assertRaises(ToolFailure):
            cabinet.ei_requests(self.ctx(), {})
        rows = json.loads(cabinet.ei_requests(self.ctx(self.boss), {}).content)
        self.assertEqual(rows[0]["suggested_employee_id"], self.me.pk)

    def test_confirm_creates_work_order(self):
        res = cabinet.ei_request_propose(self.ctx(self.boss), {"request_id": self.req.pk, "op": "confirm",
                                                               "employee_id": self.me.pk})
        action = self.confirm(res)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.CONFIRMED)
        wo = WorkOrder.objects.get(pk=action.result["work_order_id"])
        self.assertEqual((wo.assigned_employee, wo.scheduled_date), (self.me, dt.date(2026, 10, 22)))

    def test_reject(self):
        res = cabinet.ei_request_propose(self.ctx(self.boss), {"request_id": self.req.pk, "op": "spam"})
        self.confirm(res)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.SPAM)


class MeTests(Base):
    def test_me(self):
        EmployeeAvailability.objects.create(employee=self.me, date=TODAY + dt.timedelta(days=1))
        data = json.loads(cabinet.ei_me(self.ctx(), {}).content)
        self.assertEqual(data["employee"]["full_name"], "Стариков Алексей")
        self.assertEqual(len(data["availability_next_14_days"]), 1)
        self.assertFalse(data["can_manage_others"])
