"""Удаление и правка: приборы, приёмки, типы СИ, закупки, заявки, шаблоны, аттестации."""

from __future__ import annotations

import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Employee, Role, User
from apps.verification.models import (
    Instrument,
    Protocol,
    ProtocolStatus,
    Verification,
    VerificationStatus,
    WorkOrder,
)
from apps.verification.numbering import scope_for


def client_for(role: str, email: str, tab: str) -> tuple[APIClient, Employee, User]:
    user = User.objects.create_user(email=email, password="pass12345", role=role)
    employee = Employee.objects.create(tab_number=tab, full_name=email, user=user)
    api = APIClient()
    api.force_authenticate(user)
    return api, employee, user


class OrderDeletionTests(TestCase):
    def setUp(self) -> None:
        self.manager, self.me, _ = client_for(Role.MANAGER, "m@ei.test", "02")
        self.verifier, self.ver, _ = client_for(Role.VERIFIER, "v@ei.test", "03")
        self.family, _ = MeasurementFamily.objects.get_or_create(code="pressure", defaults={"name": "Манометры"})
        from apps.catalog.models import NumberingSeries

        NumberingSeries.objects.create(code="03", name="Основная", is_default=True)
        self.si = SiType.objects.create(registry_number="70436-18", name="Манометр ТМ-510", family=self.family)

    def order(self, api: APIClient) -> int:
        response = api.post("/api/orders/", {"inn": "4345000249", "name": "МУП «Водоканал»"}, format="json")
        assert response.status_code == 201, response.data
        return response.data["id"]

    def add(self, order_id: int, serial: str, employee: Employee, **extra) -> Verification:
        order = WorkOrder.objects.get(pk=order_id)
        instrument = Instrument.objects.create(si_type=self.si, serial_number=serial, owner=order.client, site=order.site)
        return Verification.objects.create(
            client_id=uuid.uuid4(), work_order=order, instrument=instrument, verifier=employee,
            verified_at=timezone.now(), **extra,
        )

    def protocol(self, verification: Verification, status: str) -> Protocol:
        return Protocol.objects.create(verification=verification, scope=scope_for(verification), status=status)

    def test_manager_deletes_order_with_drafts(self):
        order_id = self.order(self.manager)
        v = self.add(order_id, "A1", self.me)
        self.protocol(v, ProtocolStatus.NUMBERED)
        assert self.manager.delete(f"/api/orders/{order_id}/").status_code == 204
        assert not WorkOrder.objects.filter(pk=order_id).exists()
        assert not Verification.objects.filter(pk=v.pk).exists()
        assert not Instrument.objects.filter(serial_number="A1").exists()

    def test_sealed_protocol_blocks_order_and_instrument(self):
        order_id = self.order(self.manager)
        v = self.add(order_id, "A2", self.me)
        self.protocol(v, ProtocolStatus.SIGNED)
        response = self.manager.delete(f"/api/orders/{order_id}/")
        assert response.status_code == 403 and "подписан" in str(response.data["detail"])
        assert self.manager.delete(f"/api/verifications/{v.pk}/").status_code == 403
        assert self.manager.patch(f"/api/verifications/{v.pk}/", {"serial_number": "X"}, format="json").status_code == 403
        assert WorkOrder.objects.filter(pk=order_id).exists()

    def test_verifier_deletes_own_but_not_accepted_or_foreign(self):
        order_id = self.order(self.verifier)
        own = self.add(order_id, "B1", self.ver)
        accepted = self.add(order_id, "B2", self.ver, status=VerificationStatus.ACCEPTED)
        assert self.verifier.delete(f"/api/verifications/{own.pk}/").status_code == 204
        assert self.verifier.delete(f"/api/verifications/{accepted.pk}/").status_code == 403
        assert self.verifier.delete(f"/api/orders/{order_id}/").status_code == 403
        # чужая приёмка поверителю не видна
        foreign_id = self.order(self.manager)
        assert self.verifier.delete(f"/api/orders/{foreign_id}/").status_code == 404
        # а руководство удаляет и с принятой поверкой
        assert self.manager.delete(f"/api/orders/{order_id}/").status_code == 204

    def test_fix_serial_and_type(self):
        order_id = self.order(self.manager)
        v = self.add(order_id, "TYPO", self.me, measurements={"x": 1}, status=VerificationStatus.READY)
        other = SiType.objects.create(registry_number="11111-11", name="Манометр МП-100", family=self.family)
        response = self.manager.patch(f"/api/verifications/{v.pk}/", {"serial_number": "  C 1 "}, format="json")
        assert response.status_code == 200 and response.data["serial_number"] == "C 1"
        v.refresh_from_db()
        assert v.status == VerificationStatus.READY and v.measurements == {"x": 1}
        response = self.manager.patch(f"/api/verifications/{v.pk}/", {"si_type_id": other.pk}, format="json")
        assert response.status_code == 200 and response.data["si_type_id"] == other.pk
        v.refresh_from_db()
        assert v.status == VerificationStatus.DRAFT and v.measurements == {}
        assert Instrument.objects.filter(serial_number__in=["TYPO", "C 1"]).count() == 1

    def test_fix_serial_to_existing_instrument_merges(self):
        order_id = self.order(self.manager)
        v = self.add(order_id, "D1", self.me)
        Instrument.objects.create(si_type=self.si, serial_number="D2")
        response = self.manager.patch(f"/api/verifications/{v.pk}/", {"serial_number": "D2"}, format="json")
        assert response.status_code == 200
        assert not Instrument.objects.filter(serial_number="D1").exists()


class CatalogDeletionTests(TestCase):
    def setUp(self) -> None:
        self.metrologist, self.metr, self.metr_user = client_for(Role.METROLOGIST, "met@ei.test", "01")
        self.verifier, self.ver, self.ver_user = client_for(Role.VERIFIER, "v@ei.test", "03")
        self.family, _ = MeasurementFamily.objects.get_or_create(code="pressure", defaults={"name": "Манометры"})

    def test_unused_type_is_deleted_used_is_hidden(self):
        unused = SiType.objects.create(registry_number="1-01", name="А", family=self.family)
        used = SiType.objects.create(registry_number="2-02", name="Б", family=self.family)
        Instrument.objects.create(si_type=used, serial_number="1")
        assert self.metrologist.delete(f"/api/catalog/si-types/{unused.pk}/").status_code == 204
        assert not SiType.objects.filter(pk=unused.pk).exists()
        response = self.metrologist.delete(f"/api/catalog/si-types/{used.pk}/")
        assert response.status_code == 200 and response.data["archived"] is True
        used.refresh_from_db()
        assert used.is_active is False
        ids = [row["id"] for row in self.metrologist.get("/api/catalog/si-types/").data]
        assert used.pk not in ids

    def test_verifier_deletes_only_own_unreviewed(self):
        own = SiType.objects.create(registry_number="3-03", name="В", family=self.family,
                                    needs_review=True, created_by=self.ver_user)
        reviewed = SiType.objects.create(registry_number="4-04", name="Г", family=self.family)
        assert self.verifier.delete(f"/api/catalog/si-types/{reviewed.pk}/").status_code == 403
        assert self.verifier.delete(f"/api/catalog/si-types/{own.pk}/").status_code == 204

    def test_request_without_orders_is_deleted(self):
        from apps.hub.models import Request

        manager, _, _ = client_for(Role.MANAGER, "m@ei.test", "02")
        req = Request.objects.create(contact_name="Тест", contact_phone="+79000000000", address="Киров")
        assert self.verifier.delete(f"/api/hub/requests/{req.pk}/").status_code == 403
        assert manager.delete(f"/api/hub/requests/{req.pk}/").status_code == 204
        assert not Request.objects.filter(pk=req.pk).exists()

    def test_procurement_delete(self):
        from apps.logistics.models import Procurement

        manager, _, _ = client_for(Role.MANAGER, "m@ei.test", "02")
        from apps.verification.models import Client

        item = Procurement.objects.create(title="Поверка манометров", customer=Client.objects.create(name="Заказчик"))
        assert self.verifier.delete(f"/api/procurements/{item.pk}/").status_code == 403
        assert manager.delete(f"/api/procurements/{item.pk}/").status_code == 204
        assert not Procurement.objects.filter(pk=item.pk).exists()
