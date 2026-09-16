"""Календарь доступности сотрудников и публичные слоты для формы заявки."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import District, MeasurementFamily
from apps.core.models import Employee, Role, User
from apps.hub.models import AvailabilityKind, DistrictAssignment, EmployeeAvailability, RequestItem


class PublicSlotsTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        self.district = District.objects.create(name="Нововятский район")
        self.employee = Employee.objects.create(tab_number="21", full_name="Выездной В. В.")
        DistrictAssignment.objects.create(district=self.district, employee=self.employee, priority=10)

    def test_unknown_district_returns_empty_without_creating_one(self):
        response = self.api.get(reverse("availability_public_slots"), {"district": "Нет такого района"})
        assert response.status_code == 200
        assert response.data == {"district_known": False, "slots": []}
        assert not District.objects.filter(name="Нет такого района").exists()

    def test_known_district_lists_slots_without_naming_the_employee(self):
        tomorrow = timezone.localdate() + dt.timedelta(days=1)
        EmployeeAvailability.objects.create(
            employee=self.employee, kind=AvailabilityKind.DISTRICT, date=tomorrow,
            start_time=dt.time(9, 0), end_time=dt.time(11, 0), is_priority=True,
        )
        response = self.api.get(reverse("availability_public_slots"), {"district": self.district.name})
        assert response.status_code == 200
        assert response.data["district_known"] is True
        assert len(response.data["slots"]) == 1
        slot = response.data["slots"][0]
        assert slot["is_priority"] is True
        assert "employee" not in slot

    def test_trip_slots_are_not_offered_to_the_public_form(self):
        tomorrow = timezone.localdate() + dt.timedelta(days=1)
        EmployeeAvailability.objects.create(
            employee=self.employee, kind=AvailabilityKind.TRIP, date=tomorrow,
        )
        response = self.api.get(reverse("availability_public_slots"), {"district": self.district.name})
        assert response.data["slots"] == []


class MyAvailabilityTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        user = User.objects.create_user(email="verifier@ei.test", password="pass12345", role=Role.VERIFIER)
        self.employee = Employee.objects.create(tab_number="30", full_name="Свой С. С.", user=user)
        other_user = User.objects.create_user(email="other@ei.test", password="pass12345", role=Role.VERIFIER)
        self.other_employee = Employee.objects.create(tab_number="31", full_name="Чужой Ч. Ч.", user=other_user)
        self.api.force_authenticate(user)

    def test_creates_a_slot_for_the_logged_in_employee(self):
        response = self.api.post(
            reverse("availability_list"),
            {"kind": "district", "date": (timezone.localdate() + dt.timedelta(days=2)).isoformat()},
            format="json",
        )
        assert response.status_code == 201
        assert EmployeeAvailability.objects.get().employee_id == self.employee.id

    def test_cannot_create_a_slot_for_someone_else(self):
        response = self.api.post(
            reverse("availability_list"),
            {
                "employee": self.other_employee.id, "kind": "district",
                "date": (timezone.localdate() + dt.timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        assert response.status_code == 403

    def test_list_only_returns_own_slots_by_default(self):
        EmployeeAvailability.objects.create(employee=self.employee, date=timezone.localdate())
        EmployeeAvailability.objects.create(employee=self.other_employee, date=timezone.localdate())
        response = self.api.get(reverse("availability_list"))
        assert response.data["count"] == 1

    def test_cannot_view_someone_elses_slots_without_a_privileged_role(self):
        response = self.api.get(reverse("availability_list"), {"employee": self.other_employee.id})
        assert response.status_code == 403

    def test_cannot_delete_someone_elses_slot(self):
        slot = EmployeeAvailability.objects.create(employee=self.other_employee, date=timezone.localdate())
        response = self.api.delete(reverse("availability_detail", args=[slot.id]))
        assert response.status_code == 403
        assert EmployeeAvailability.objects.filter(pk=slot.id).exists()

    def test_bulk_create_adds_the_same_slot_on_several_dates(self):
        dates = [
            (timezone.localdate() + dt.timedelta(days=offset)).isoformat() for offset in (1, 2, 5)
        ]
        response = self.api.post(
            reverse("availability_bulk"),
            {"dates": dates, "kind": "district", "is_priority": True, "note": "неделя открыта"},
            format="json",
        )
        assert response.status_code == 201
        assert len(response.data) == 3
        created = EmployeeAvailability.objects.filter(employee=self.employee)
        assert created.count() == 3
        assert set(created.values_list("date", flat=True)) == {
            dt.date.fromisoformat(d) for d in dates
        }
        assert all(slot.is_priority for slot in created)

    def test_bulk_create_validates_time_order(self):
        response = self.api.post(
            reverse("availability_bulk"),
            {
                "dates": [(timezone.localdate() + dt.timedelta(days=1)).isoformat()],
                "start_time": "18:00", "end_time": "09:00",
            },
            format="json",
        )
        assert response.status_code == 400
        assert EmployeeAvailability.objects.count() == 0

    def test_bulk_create_always_uses_the_authenticated_employee(self):
        response = self.api.post(
            reverse("availability_bulk"),
            {"dates": [(timezone.localdate() + dt.timedelta(days=1)).isoformat()]},
            format="json",
        )
        assert response.status_code == 201
        assert EmployeeAvailability.objects.get().employee_id == self.employee.id


class RequestWithItemsTestCase(TestCase):
    """Заявка с формы сайта: несколько типов приборов + примерная цена (снимок на сервере)."""

    def setUp(self) -> None:
        self.api = APIClient()
        # См. комментарий в test_api.py.RequestCreateTestCase.setUp — тот же
        # общий на весь прогон AnonRateThrottle.
        cache.clear()
        self.water = MeasurementFamily.objects.create(
            code="water-meter", name="Счётчики воды", price=Decimal("500.00"), requires_time_slot=True
        )
        self.scales = MeasurementFamily.objects.create(
            code="scales", name="Весы", price=Decimal("1000.00"), requires_time_slot=False
        )

    def payload(self, **overrides):
        data = {
            "contact_name": "Заявитель",
            "contact_phone": "+7 (999) 000-00-00",
            "address": "г. Киров, ул. Ленина, 1",
            "items": [{"family_id": self.water.id, "quantity": 2}, {"family_id": self.scales.id, "quantity": 1}],
            "consent_given": True,
        }
        data.update(overrides)
        return data

    def test_stores_items_and_a_server_side_price_snapshot(self):
        response = self.api.post(reverse("request_create"), self.payload(), format="json")
        assert response.status_code == 201
        items = RequestItem.objects.filter(request_id=response.data["id"])
        assert items.count() == 2
        request_obj = items.first().request
        assert request_obj.estimated_price == Decimal("2000.00")  # 2*500 + 1*1000, без скидки

    def test_ignores_a_client_supplied_price_and_recomputes_it(self):
        response = self.api.post(
            reverse("request_create"),
            self.payload(estimated_price="1.00", is_priority_slot=True),
            format="json",
        )
        request_obj = RequestItem.objects.filter(request_id=response.data["id"]).first().request
        assert request_obj.estimated_price == Decimal("1800.00")  # скидка 10% на 2000

    def test_priority_slot_is_ignored_without_a_time_required_item(self):
        response = self.api.post(
            reverse("request_create"),
            self.payload(items=[{"family_id": self.scales.id, "quantity": 1}], is_priority_slot=True),
            format="json",
        )
        request_obj = RequestItem.objects.filter(request_id=response.data["id"]).first().request
        assert request_obj.is_priority_slot is False
        assert request_obj.discount_percent == Decimal("0")

    def test_desired_time_is_dropped_when_nothing_needs_it(self):
        response = self.api.post(
            reverse("request_create"),
            self.payload(
                items=[{"family_id": self.scales.id, "quantity": 1}],
                desired_time="10:00",
            ),
            format="json",
        )
        request_obj = RequestItem.objects.filter(request_id=response.data["id"]).first().request
        assert request_obj.desired_time is None

    def test_still_accepts_the_legacy_free_text_description_without_items(self):
        response = self.api.post(
            reverse("request_create"),
            {
                "contact_name": "Заявитель",
                "contact_phone": "+7 (999) 000-00-00",
                "address": "г. Киров, ул. Ленина, 1",
                "si_description": "Манометр, 1 шт.",
                "consent_given": True,
            },
            format="json",
        )
        assert response.status_code == 201
