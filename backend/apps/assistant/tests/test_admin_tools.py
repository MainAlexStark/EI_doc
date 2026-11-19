"""Администрирование у ассистента: вся фирма, назначение пачкой, сотрудники — через карточку подтверждения."""

from __future__ import annotations

import datetime as dt
import json

from django.test import TestCase

from apps.assistant import actions, admin_tools
from apps.assistant.models import Conversation, PendingAction
from apps.assistant.tools import ToolContext, ToolFailure, all_tools
from apps.core.models import Employee, Role, User
from apps.hub.models import Request, RequestStatus
from apps.logistics.models import TripStop
from apps.logistics.service import attach_to_city_day
from apps.verification.models import Client, Site, WorkOrder


class AdminToolsTests(TestCase):
    def setUp(self) -> None:
        self.boss = User.objects.create_user("h@ei.test", "pw", role=Role.HEAD)
        Employee.objects.create(tab_number="01", full_name="Руководитель Р. Р.", user=self.boss)
        self.manager = User.objects.create_user("m@ei.test", "pw", role=Role.MANAGER)
        self.verifier_user = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.a = Employee.objects.create(tab_number="05", full_name="Кузнецов Иван", user=self.verifier_user)
        self.b = Employee.objects.create(tab_number="06", full_name="Петров Пётр")

    def ctx(self, user) -> ToolContext:
        return ToolContext(user=user, conversation=Conversation.objects.create(user=user))

    def confirm(self, result) -> PendingAction:
        action = PendingAction.objects.get(pk=result.ui["action"])
        return actions.decide(action, action.conversation.user, confirm=True)

    def test_registered(self):
        tools, _ = all_tools(with_mcp=False)
        for name in ("ei_team", "ei_dispatch_board", "ei_team_schedule", "ei_assign_propose", "ei_staff",
                     "ei_staff_propose"):
            self.assertIn(name, tools)

    def test_verifier_cannot(self):
        with self.assertRaises(ToolFailure):
            admin_tools.ei_team(self.ctx(self.verifier_user), {})
        with self.assertRaises(ToolFailure):
            admin_tools.ei_staff_propose(self.ctx(self.manager), {"op": "create", "full_name": "Х Х"})

    def test_assign_queue_in_one_card(self):
        day = dt.date.today() + dt.timedelta(days=1)
        req = Request.objects.create(contact_name="Иванова", address="г Киров, ул Мира, 2", consent_given=True)
        client = Client.objects.create(name="ООО Ромашка")
        order = WorkOrder.objects.create(client=client, site=Site.objects.create(client=client, address="ул Мира, 5"),
                                         assigned_employee=self.a, scheduled_date=day)
        stop = attach_to_city_day(order)
        board = json.loads(admin_tools.ei_dispatch_board(self.ctx(self.manager), {"date_from": day.isoformat()}).content)
        assert board["unassigned_queue"][0]["id"] == req.pk
        res = admin_tools.ei_assign_propose(self.ctx(self.manager), {
            "items": [{"type": "request", "id": req.pk}, {"type": "stop", "id": stop.pk}],
            "employee_id": self.b.pk, "date": day.isoformat(), "time": "10:00",
        })
        assert TripStop.objects.get(pk=stop.pk).trip.lead == self.a  # до подтверждения ничего
        action = self.confirm(res)
        assert action.status == PendingAction.Status.DONE, action.error
        req.refresh_from_db()
        assert req.status == RequestStatus.CONFIRMED and TripStop.objects.get(pk=stop.pk).trip.lead == self.b
        team = json.loads(admin_tools.ei_team(self.ctx(self.manager), {"date": day.isoformat()}).content)
        row = next(e for e in team["employees"] if e["id"] == self.b.pk)
        assert row["stops"]["total"] == 2

    def test_staff_create_password_hidden_from_model(self):
        res = admin_tools.ei_staff_propose(self.ctx(self.boss), {
            "op": "create", "full_name": "Новиков Николай", "email": "n@ei.test", "role": "verifier",
        })
        action = self.confirm(res)
        assert action.status == PendingAction.Status.DONE, action.error
        password = action.result["password"]
        assert User.objects.get(email="n@ei.test").check_password(password)
        event = action.conversation.messages.order_by("-id").first()
        assert password not in event.content and "Новиков Николай" in event.content
        emp = Employee.objects.get(full_name="Новиков Николай")
        action = self.confirm(admin_tools.ei_staff_propose(self.ctx(self.boss), {"op": "update", "employee_id": emp.pk,
                                                                                  "role": "manager"}))
        assert User.objects.get(email="n@ei.test").role == Role.MANAGER
        action = self.confirm(admin_tools.ei_staff_propose(self.ctx(self.boss), {"op": "deactivate", "employee_id": emp.pk}))
        assert not User.objects.get(email="n@ei.test").is_active
