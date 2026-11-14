"""Заказы во вкладке «Поверки»: заказчик по ИНН, приём, выдача, акты, закупка."""

from __future__ import annotations

import shutil
import uuid
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Employee, Role, User
from apps.logistics.models import Procurement
from apps.logistics.service import procurement_progress
from apps.verification.models import (
    Client,
    Instrument,
    Verification,
    VerificationStatus,
    WorkOrder,
    WorkOrderKind,
)


class OrderTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(email="m@ei.test", password="pass12345", role=Role.MANAGER)
        self.me = Employee.objects.create(tab_number="02", full_name="Стариков А. С.", user=user)
        self.api = APIClient()
        self.api.force_authenticate(user)
        stranger = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        Employee.objects.create(tab_number="03", full_name="Чужой", user=stranger)
        self.stranger = APIClient()
        self.stranger.force_authenticate(stranger)

    def create(self, **extra) -> dict:
        body = {"inn": "4345000249", "name": "МУП «Водоканал»", "address": "610000, г Киров, ул Пролетарская, д 15",
                "kpp": "434501001", "contact": "Петров И. И., инженер", **extra}
        response = self.api.post("/api/orders/", body, format="json")
        assert response.status_code == 201, response.data
        return response.data

    def add(self, order_id: int, serial: str, *, done=False, suitable=True) -> Verification:
        family, _ = MeasurementFamily.objects.get_or_create(code="pressure", defaults={"name": "Манометры"})
        si, _ = SiType.objects.get_or_create(registry_number="70436-18", defaults={"name": "Манометр ТМ-510", "family": family})
        order = WorkOrder.objects.get(pk=order_id)
        instrument = Instrument.objects.create(si_type=si, serial_number=serial, owner=order.client, site=order.site)
        return Verification.objects.create(
            client_id=uuid.uuid4(), work_order=order, instrument=instrument, verifier=self.me, verified_at=timezone.now(),
            status=VerificationStatus.READY if done else VerificationStatus.DRAFT, suitable=suitable,
        )

    def test_receive_creates_lab_order_for_the_org(self):
        data = self.create()
        assert data["kind"] == "lab" and data["status"] == "in_progress" and data["received_at"]
        assert data["client"] == "МУП «Водоканал»" and data["client_inn"] == "4345000249"
        assert data["site"] == "610000, г Киров, ул Пролетарская, д 15" and data["trip_id"] is None
        client = Client.objects.get(inn="4345000249")
        assert client.kind == "org" and client.kpp == "434501001"
        # тот же заказчик второй раз — та же карточка
        self.create(address="")
        assert Client.objects.filter(inn="4345000249").count() == 1
        ids = [row["id"] for row in self.api.get("/api/work-orders/", {"kind": "lab"}).data]
        assert len(ids) == 2

    def test_bad_inn_and_no_customer(self):
        assert self.api.post("/api/orders/", {"inn": "123"}, format="json").status_code == 400
        assert self.api.post("/api/orders/", {}, format="json").status_code == 400

    def test_lookup_own_then_dadata(self):
        assert self.api.get("/api/clients/lookup/", {"inn": "4345000249"}).data["found"] is False
        self.create()
        own = self.api.get("/api/clients/lookup/", {"inn": "4345000249"}).data
        assert own["found"] and own["source"] == "own" and own["kpp"] == "434501001"
        reply = mock.Mock(**{"json.return_value": {"suggestions": [{"value": "ООО Ромашка", "data": {
            "inn": "7707083893", "kpp": "773601001", "ogrn": "1027700132195", "name": {"short_with_opf": "ООО «Ромашка»"},
            "address": {"value": "г Москва, ул Вавилова, д 19"}, "state": {"status": "ACTIVE"}}}]},
            "raise_for_status.return_value": None})
        with override_settings(DADATA_API_KEY="k"), mock.patch("apps.verification.party.requests.post", return_value=reply):
            found = self.api.get("/api/clients/lookup/", {"inn": "7707083893"}).data
        assert found["source"] == "dadata" and found["name"] == "ООО «Ромашка»" and found["address"].startswith("г Москва")

    def test_suggest_by_name_own_then_dadata(self):
        assert self.api.get("/api/clients/suggest/", {"q": "в"}).data["results"] == []
        self.create()
        Client.objects.create(name="Иванов И. И.", kind="person")  # частник с выезда — не заказчик
        own = self.api.get("/api/clients/suggest/", {"q": "Водоканал"}).data["results"]
        assert [p["inn"] for p in own] == ["4345000249"] and own[0]["source"] == "own" and own[0]["client_id"]
        assert self.api.get("/api/clients/suggest/", {"q": "4345"}).data["results"][0]["inn"] == "4345000249"
        assert self.api.get("/api/clients/suggest/", {"q": "Иванов"}).data["results"] == []
        reply = mock.Mock(**{"json.return_value": {"suggestions": [
            {"value": "МУП Водоканал", "data": {"inn": "4345000249", "ogrn": "1024301340000",
             "name": {"short_with_opf": "МУП «Водоканал»"}, "address": {"value": "г Киров"}, "state": {"status": "ACTIVE"}}},
            {"value": "ООО Водоканал-Сервис", "data": {"inn": "4345111111", "kpp": "434501001",
             "name": {"short_with_opf": "ООО «Водоканал-Сервис»"}, "address": {"value": "г Киров, ул Ленина, д 1"},
             "state": {"status": "LIQUIDATED"}}},
        ]}, "raise_for_status.return_value": None})
        with override_settings(DADATA_API_KEY="k"), mock.patch("apps.verification.party.requests.post", return_value=reply) as post:
            found = self.api.get("/api/clients/suggest/", {"q": "Водоканал"}).data["results"]
        assert post.call_args.kwargs["json"]["locations_boost"] == [{"kladr_id": "43"}]
        assert [p["inn"] for p in found] == ["4345000249", "4345111111"]  # своя запись не дублируется
        assert found[0]["source"] == "own" and found[0]["ogrn"] == "1024301340000"
        assert found[1]["source"] == "dadata" and found[1]["status"] == "LIQUIDATED"

    def test_issue_and_procurement_progress(self):
        customer = Client.objects.create(name="МУП «Водоканал»", inn="4345000249", kind="org")
        procurement = Procurement.objects.create(title="Поверка манометров", number="0340", customer=customer)
        data = self.create(procurement_id=procurement.pk)
        assert data["procurement_label"].startswith("№ 0340")
        assert self.api.post(f"/api/orders/{data['id']}/issue/", {}, format="json").status_code == 400  # пусто
        self.add(data["id"], "M1", done=True)
        self.add(data["id"], "M2", done=True, suitable=False)
        assert procurement_progress(procurement)["ready"] == 2
        issued = self.api.post(f"/api/orders/{data['id']}/issue/", {"issued": True}, format="json")
        assert issued.status_code == 200 and issued.data["issued_at"]
        back = self.api.post(f"/api/orders/{data['id']}/issue/", {"issued": False}, format="json")
        assert back.data["issued_at"] is None

    def test_patch_customer_and_contact(self):
        data = self.create()
        response = self.api.patch(f"/api/orders/{data['id']}/", {"contact": "Сидоров", "address": "г Киров, ул Новая, 1"},
                                  format="json")
        assert response.data["contact"] == "Сидоров" and response.data["site"] == "г Киров, ул Новая, 1"

    def test_strangers_do_not_see_orders(self):
        data = self.create()
        assert self.stranger.post(f"/api/orders/{data['id']}/issue/", {}, format="json").status_code == 404
        assert self.stranger.get(f"/api/orders/{data['id']}/act/receive.pdf").status_code == 404

    def test_field_work_order_is_not_an_order(self):
        data = self.create()
        WorkOrder.objects.filter(pk=data["id"]).update(kind=WorkOrderKind.FIELD)
        assert self.api.get(f"/api/orders/{data['id']}/act/receive.pdf").status_code == 404

    def test_acts_render(self):
        if not shutil.which("typst"):
            self.skipTest("typst не установлен")
        data = self.create()
        assert self.api.get(f"/api/orders/{data['id']}/act/receive.pdf").status_code == 400  # нет приборов
        self.add(data["id"], "M1", done=True)
        self.add(data["id"], "M2")
        for kind in ("receive", "issue"):
            response = self.api.get(f"/api/orders/{data['id']}/act/{kind}.pdf")
            assert response.status_code == 200, response.content[:300]
            assert response.content.startswith(b"%PDF") and "attachment" in response["Content-Disposition"]
        assert self.api.get(f"/api/orders/{data['id']}/act/other.pdf").status_code == 404
