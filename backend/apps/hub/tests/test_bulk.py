"""Режим «Заявки»: подтвердить / назначить / отклонить / спам — пачкой."""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
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

    def test_reopen_returns_to_new(self):
        self.post(action="reject", ids=[self.ids[0]], reason="дубль")
        self.post(action="spam", ids=[self.ids[1]])
        self.post(action="assign", ids=[self.ids[2]], employee_id=self.worker.pk)
        self.post(action="confirm", ids=[self.ids[3]], employee_id=self.worker.pk)
        r = self.post(action="reopen", ids=self.ids)
        assert r.status_code == 200 and r.data["done"] == self.ids, r.data
        for row in Request.objects.filter(pk__in=self.ids):
            assert row.status == RequestStatus.NEW and row.reject_reason == ""
            assert row.suggested_employee_id is None and row.assigned_employee_id is None
        assert WorkOrder.objects.get(request_id=self.ids[3]).status == "cancelled"
        again = self.post(action="reopen", ids=self.ids[:1])
        assert again.data["skipped"] == [{"id": self.ids[0], "reason": "уже во «Новых»"}]
        body = {"action": "reopen", "ids": self.ids}
        assert self.verifier_api.post("/api/hub/requests/bulk/", body, format="json").status_code == 403

    def test_reopen_refused_when_verifications_exist(self):
        from django.utils import timezone

        from apps.verification.models import Verification
        from apps.verification.tests.factories import make_family, make_instrument

        self.post(action="confirm", ids=[self.ids[0]], employee_id=self.worker.pk)
        order = WorkOrder.objects.get(request_id=self.ids[0])
        Verification.objects.create(work_order=order, instrument=make_instrument(make_family(), "SN1"),
                                    verifier=self.worker, verified_at=timezone.now())
        r = self.post(action="reopen", ids=[self.ids[0]])
        assert r.data["done"] == [] and "поверки" in r.data["errors"][0]["error"]
        assert Request.objects.get(pk=self.ids[0]).status == RequestStatus.CONFIRMED
        assert WorkOrder.objects.get(pk=order.pk).status != "cancelled"

    def test_last_change_in_list_without_n_plus_one(self):
        api = self.api
        api.post("/api/hub/requests/bulk/", {"action": "reject", "ids": self.ids[:1], "reason": "дубль"}, format="json")
        r = api.get(reverse("request_list"))
        assert r.status_code == 200, (r.status_code, r.data)
        rows = {row["id"]: row for row in r.data}
        assert rows[self.ids[0]]["updated_by"] == "Менеджер" and rows[self.ids[0]]["updated_at"]
        assert rows[self.ids[1]]["updated_by"] == ""  # заведена без входа
        with self.assertNumQueries(2):  # заявки с подзапросами «последний изменил», приборы
            api.get(reverse("request_list"))

    def test_confirm_twice_with_stale_object_makes_one_work_order(self):
        from apps.hub import service

        stale = Request.objects.get(pk=self.ids[0])  # второй диспетчер открыл заявку раньше
        r = self.post(action="confirm", ids=[self.ids[0]], employee_id=self.worker.pk)
        assert r.data["done"] == [self.ids[0]]
        try:
            service.confirm_request(stale, employee=self.worker)
        except service.AlreadyConfirmed:
            pass
        else:
            raise AssertionError("повторное подтверждение прошло")
        r = self.api.post(f"/api/hub/requests/{self.ids[0]}/confirm/", {"employee_id": self.worker.pk}, format="json")
        assert r.status_code == 400
        assert WorkOrder.objects.filter(request_id=self.ids[0]).count() == 1

    def test_verifier_sees_only_own_requests_and_cannot_confirm(self):
        mine = Employee.objects.create(tab_number="07", full_name="Петров Пётр",
                                       user=User.objects.get(email="v@ei.test"))
        self.post(action="assign", ids=[self.ids[1]], employee_id=mine.pk)
        rows = self.verifier_api.get(reverse("request_list")).data
        assert [row["id"] for row in rows] == [self.ids[1]]
        for action in ("confirm", "reject", "route"):
            r = self.verifier_api.post(f"/api/hub/requests/{self.ids[1]}/{action}/", {"employee_id": mine.pk}, format="json")
            assert r.status_code == 403, action
        assert not WorkOrder.objects.exists()
        assert len(self.api.get(reverse("request_list")).data) == 4
