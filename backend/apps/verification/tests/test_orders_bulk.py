"""Приёмка: приборы пачкой одним запросом — идемпотентно."""

from __future__ import annotations

import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Employee, Role, User
from apps.verification.models import Instrument, Verification


class OrderInstrumentsBulkTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        Employee.objects.create(tab_number="02", full_name="Стариков А. С.", user=user)
        self.api = APIClient()
        self.api.force_authenticate(user)
        family = MeasurementFamily.objects.create(code="pressure", name="Манометры")
        self.si = SiType.objects.create(registry_number="12345-20", name="МП-100", family=family)
        order = self.api.post("/api/orders/", {"inn": "4345000249", "name": "МУП «Водоканал»"}, format="json")
        self.url = f"/api/orders/{order.data['id']}/instruments/bulk/"
        self.order_id = order.data["id"]

    def test_rows_in_one_request_and_repeat_is_safe(self):
        rows = [{"si_type_id": self.si.pk, "serial_number": f"A{n}", "manufacture_year": 2020 + n} for n in range(5)]
        rows[0]["client_id"] = str(uuid.uuid4())
        first = self.api.post(self.url, {"rows": rows}, format="json")
        assert first.status_code == 201, first.data
        assert len(first.data["created"]) == 5 and first.data["errors"] == []
        assert Instrument.objects.get(serial_number="A3").manufacture_year == 2023

        again = self.api.post(self.url, {"rows": rows + [{"si_type_id": 999, "serial_number": "X"}]}, format="json")
        assert again.status_code == 200
        assert again.data["created"] == [] and len(again.data["existing"]) == 5
        assert again.data["errors"][0]["index"] == 5
        assert Verification.objects.filter(work_order_id=self.order_id).count() == 5
        listed = self.api.get(f"/api/work-orders/{self.order_id}/verifications/").data
        assert {row["serial_number"] for row in listed} == {f"A{n}" for n in range(5)}

    def test_limits(self):
        assert self.api.post(self.url, {"rows": []}, format="json").status_code == 400
        too_many = [{"si_type_id": self.si.pk, "serial_number": str(n)} for n in range(301)]
        assert self.api.post(self.url, {"rows": too_many}, format="json").status_code == 400
