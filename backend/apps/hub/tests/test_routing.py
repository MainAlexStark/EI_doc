"""Роутинг заявок по району."""

from __future__ import annotations

from django.test import TestCase

from apps.catalog.models import District
from apps.core.models import Employee
from apps.hub.models import DistrictAssignment, Request, RequestStatus
from apps.hub.routing import resolve_district, route


def make_request(**overrides) -> Request:
    defaults = dict(contact_name="Иванов И. И.", contact_phone="+79990000000", address="г. Киров, ул. Ленина 1")
    defaults.update(overrides)
    return Request.objects.create(**defaults)


class ResolveDistrictTestCase(TestCase):
    def test_creates_district_by_name(self):
        district = resolve_district("Ленинский район")
        assert district is not None
        assert district.name == "Ленинский район"

    def test_reuses_existing_district(self):
        first = resolve_district("Октябрьский район")
        second = resolve_district("Октябрьский район")
        assert first.pk == second.pk

    def test_blank_name_resolves_to_nothing(self):
        assert resolve_district("") is None
        assert resolve_district("   ") is None


class RouteTestCase(TestCase):
    def setUp(self) -> None:
        self.district = District.objects.create(name="Ленинский район")
        self.preferred = Employee.objects.create(tab_number="01", full_name="Стариков А. А.")
        self.backup = Employee.objects.create(tab_number="02", full_name="Резервный Р. Р.")

    def test_suggests_the_highest_priority_active_assignment(self):
        DistrictAssignment.objects.create(district=self.district, employee=self.backup, priority=50)
        DistrictAssignment.objects.create(district=self.district, employee=self.preferred, priority=10)

        request_obj = make_request(district=self.district)
        route(request_obj)

        assert request_obj.status == RequestStatus.ROUTED
        assert request_obj.suggested_employee_id == self.preferred.pk
        assert request_obj.routed_at is not None

    def test_ignores_inactive_assignments(self):
        DistrictAssignment.objects.create(
            district=self.district, employee=self.preferred, priority=10, is_active=False
        )
        request_obj = make_request(district=self.district)
        route(request_obj)
        assert request_obj.suggested_employee is None

    def test_no_district_leaves_suggestion_empty_but_still_routed(self):
        request_obj = make_request(district=None)
        route(request_obj)
        assert request_obj.status == RequestStatus.ROUTED
        assert request_obj.suggested_employee is None

    def test_does_not_touch_a_confirmed_request(self):
        DistrictAssignment.objects.create(district=self.district, employee=self.preferred, priority=10)
        request_obj = make_request(district=self.district, status=RequestStatus.CONFIRMED)
        route(request_obj)
        assert request_obj.suggested_employee is None
