"""Полевая работа: наряд → поверка, идемпотентность по client_id."""

from __future__ import annotations

import uuid

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.verification.models import Client, Instrument, Site, Verification, WorkOrder
from apps.verification.tests import factories as f


class FieldVerificationCreateTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.si_type = f.make_si_type(self.family)
        user = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        self.employee = Employee.objects.create(tab_number="07", full_name="Поверитель П. П.", user=user)
        self.client_obj = Client.objects.create(name="ООО Ромашка")
        self.site = Site.objects.create(address="г. Киров, ул. Мира, 1", client=self.client_obj)
        self.work_order = WorkOrder.objects.create(
            client=self.client_obj, site=self.site, assigned_employee=self.employee,
        )
        self.api = APIClient()
        self.api.force_authenticate(user)

    def url(self):
        return reverse("work_order_verifications", args=[self.work_order.pk])

    def payload(self, **overrides):
        return {
            "client_id": str(uuid.uuid4()),
            "si_type_id": self.si_type.id,
            "serial_number": "SN-001",
        } | overrides

    def test_creates_verification_and_instrument(self):
        response = self.api.post(self.url(), self.payload(), format="json")
        assert response.status_code == 201
        assert Verification.objects.count() == 1
        verification = Verification.objects.get()
        assert verification.work_order_id == self.work_order.id
        assert verification.verifier_id == self.employee.id
        assert verification.instrument.serial_number == "SN-001"
        assert verification.instrument.owner_id == self.client_obj.id
        assert verification.instrument.site_id == self.site.id
        assert verification.temperature is not None  # условия подставлены сами

    def test_same_client_id_twice_does_not_duplicate(self):
        body = self.payload()
        first = self.api.post(self.url(), body, format="json")
        second = self.api.post(self.url(), body, format="json")
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.data["id"] == second.data["id"]
        assert Verification.objects.count() == 1

    def test_same_instrument_reused_across_visits(self):
        self.api.post(self.url(), self.payload(), format="json")
        self.api.post(self.url(), self.payload(client_id=str(uuid.uuid4())), format="json")
        assert Instrument.objects.count() == 1
        assert Verification.objects.count() == 2

    def test_requires_linked_employee(self):
        user = User.objects.create_user(email="obs@ei.test", password="pass12345", role=Role.OBSERVER)
        api = APIClient()
        api.force_authenticate(user)
        response = api.post(self.url(), self.payload(), format="json")
        assert response.status_code == 403

    def test_list_shows_created_verifications(self):
        self.api.post(self.url(), self.payload(), format="json")
        response = self.api.get(self.url())
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["serial_number"] == "SN-001"

    def test_requires_authentication(self):
        api = APIClient()
        response = api.post(self.url(), self.payload(), format="json")
        assert response.status_code == 401
