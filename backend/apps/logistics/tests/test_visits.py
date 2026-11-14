"""Запись на поверку из сервиса: заявка «по телефону» сразу встаёт в день исполнителя."""

from __future__ import annotations

import datetime as dt

from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.models import District, MeasurementFamily
from apps.core.models import Employee, Role, User
from apps.hub.models import DistrictAssignment, Request, RequestStatus
from apps.logistics.models import TripKind, TripStop


class VisitTests(TestCase):
    def setUp(self) -> None:
        manager = User.objects.create_user(email="m@ei.test", password="pass12345", role=Role.MANAGER)
        self.me = Employee.objects.create(tab_number="01", full_name="Диспетчер Д. Д.", user=manager)
        verifier = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        self.verifier = Employee.objects.create(tab_number="05", full_name="Кузнецов И. П.", user=verifier)
        self.api = APIClient()
        self.api.force_authenticate(manager)
        self.as_verifier = APIClient()
        self.as_verifier.force_authenticate(verifier)
        self.body = {
            "contact_name": "Иванова М. П.", "contact_phone": "+79000000001",
            "address": "г Киров, ул Ленина, д 102, кв 5", "latitude": 58.5928, "longitude": 49.663,
            "date": "2026-11-18", "window_start": "08:00", "window_end": "10:00", "quantity": 3,
            "comment": "Домофон 5", "employee_id": None,
        }

    def test_booking_lands_in_the_verifier_day(self):
        response = self.api.post("/api/visits/", {**self.body, "employee_id": self.verifier.pk}, format="json")
        assert response.status_code == 201, response.data
        stop = TripStop.objects.get(pk=response.data["stop_id"])
        assert stop.trip.kind == TripKind.CITY and stop.trip.lead == self.verifier
        assert stop.trip.date_start == dt.date(2026, 11, 18) and response.data["trip_title"] == "Киров — 18 ноября"
        assert (stop.window_start, stop.window_end) == (dt.time(8, 0), dt.time(10, 0))
        assert stop.planned_count == 3 and stop.note == "Домофон 5"
        assert float(stop.site.latitude) == 58.5928 and stop.client.name == "Иванова М. П."
        request = Request.objects.get(pk=response.data["request_id"])
        assert request.source == "phone" and request.status == RequestStatus.CONFIRMED
        assert request.assigned_employee == self.verifier and request.consent_given is False
        # вторая запись на тот же день — в тот же выезд
        again = self.api.post("/api/visits/", {**self.body, "address": "г Киров, ул Ленина, 7", "employee_id": self.verifier.pk},
                              format="json")
        assert again.data["trip_id"] == response.data["trip_id"]

    def test_without_employee_routing_by_district_then_me(self):
        district = District.objects.create(name="Ленинский район")
        DistrictAssignment.objects.create(district=district, employee=self.verifier, priority=1)
        routed = self.api.post("/api/visits/", {**self.body, "district": "Ленинский район"}, format="json")
        assert routed.data["employee"] == "Кузнецов И. П."
        fallback = self.api.post("/api/visits/", {**self.body, "address": "г Киров, ул Мира, 1"}, format="json")
        assert fallback.data["employee"] == "Диспетчер Д. Д."

    def test_items_by_family_count_as_expected_instruments(self):
        family = MeasurementFamily.objects.create(code="water", name="Счётчики воды")
        response = self.api.post("/api/visits/", {**self.body, "quantity": None, "items": [{"family_id": family.pk, "quantity": 2}],
                                                  "employee_id": self.verifier.pk}, format="json")
        assert TripStop.objects.get(pk=response.data["stop_id"]).planned_count == 2

    def test_verifier_books_only_for_himself(self):
        own = self.as_verifier.post("/api/visits/", {**self.body, "employee_id": self.verifier.pk}, format="json")
        assert own.status_code == 201
        other = self.as_verifier.post("/api/visits/", {**self.body, "employee_id": self.me.pk}, format="json")
        assert other.status_code == 403

    def test_validation(self):
        assert self.api.post("/api/visits/", {**self.body, "address": " "}, format="json").status_code == 400
        bad = self.api.post("/api/visits/", {**self.body, "window_start": "10:00", "window_end": "08:00"}, format="json")
        assert bad.status_code == 400
        assert not Request.objects.exists()
