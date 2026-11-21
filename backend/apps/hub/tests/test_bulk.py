"""Режим «Заявки»: подтвердить / назначить / отклонить / спам — пачкой."""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.hub.models import Request, RequestStatus
from apps.verification.models import WorkOrder


class RequestBulkTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        boss = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        Employee.objects.create(tab_number="01", full_name="Менеджер", user=boss)
        self.worker = Employee.objects.create(tab_number="05", full_name="Кузнецов Иван")
        self.api = APIClient()
        self.api.force_authenticate(boss)
        verifier = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)
        self.verifier_api = APIClient()
        self.verifier_api.force_authenticate(verifier)
        self.requests = [
            Request.objects.create(contact_name=f"Клиент {n}", contact_phone="+7 (900) 000-00-00",
                                   address=f"г Киров, ул Мира, {n}", consent_given=True)
            for n in range(4)
        ]
        self.ids = [r.pk for r in self.requests]

    def post(self, **body):
        return self.api.post("/api/hub/requests/bulk/", body, format="json")

    def test_assign_then_confirm_with_suggested(self):
        r = self.post(action="assign", ids=self.ids[:2], employee_id=self.worker.pk)
        assert r.status_code == 200 and r.data["done"] == self.ids[:2]
        assert {row["suggested_employee_id"] for row in r.data["items"]} == {self.worker.pk}

        r = self.post(action="confirm", ids=self.ids[:3], scheduled_date="2026-12-01")
        assert r.data["done"] == self.ids[:2]
        assert r.data["errors"][0]["id"] == self.ids[2]  # исполнитель не подобран
        assert WorkOrder.objects.filter(request_id__in=self.ids[:2], assigned_employee=self.worker).count() == 2

        repeat = self.post(action="confirm", ids=self.ids[:2])
        assert repeat.data["done"] == [] and len(repeat.data["skipped"]) == 2
        assert WorkOrder.objects.count() == 2

    def test_reject_and_spam(self):
        r = self.post(action="reject", ids=self.ids[:2], reason="дубль")
        assert r.data["done"] == self.ids[:2]
        r = self.post(action="spam", ids=self.ids[2:] + [999999])
        assert r.data["done"] == self.ids[2:] and r.data["errors"] == [{"id": 999999, "error": "Заявки нет"}]
        statuses = dict(Request.objects.values_list("pk", "status"))
        assert statuses[self.ids[0]] == RequestStatus.REJECTED and statuses[self.ids[3]] == RequestStatus.SPAM
        assert Request.objects.get(pk=self.ids[0]).reject_reason == "дубль"

    def test_permissions_and_validation(self):
        body = {"action": "spam", "ids": self.ids}
        assert self.verifier_api.post("/api/hub/requests/bulk/", body, format="json").status_code == 403
        assert self.post(action="assign", ids=self.ids).status_code == 400  # кому?
        assert self.post(action="burn", ids=self.ids).status_code == 400
