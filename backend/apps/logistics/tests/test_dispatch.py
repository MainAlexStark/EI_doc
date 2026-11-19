"""Диспетчерская руководства: панель, карта, командный график, быстрое назначение, сотрудники."""

from __future__ import annotations

import datetime as dt

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.hub.models import EmployeeAvailability, Request, RequestStatus
from apps.logistics.models import Trip, TripKind, TripStop
from apps.verification.models import Client, Site, WorkOrder, WorkOrderKind

DAY = dt.date(2026, 11, 20)


class DispatchTests(TestCase):
    def setUp(self) -> None:
        boss = User.objects.create_user(email="m@ei.test", password="pass12345", role=Role.MANAGER)
        self.boss = Employee.objects.create(tab_number="01", full_name="Диспетчер Дарья Дмитриевна", user=boss)
        self.a = Employee.objects.create(
            tab_number="05", full_name="Кузнецов Иван Петрович",
            user=User.objects.create_user(email="a@ei.test", password="pass12345", role=Role.VERIFIER),
        )
        self.b = Employee.objects.create(
            tab_number="06", full_name="Петров Пётр Петрович",
            user=User.objects.create_user(email="b@ei.test", password="pass12345", role=Role.VERIFIER),
        )
        self.api = APIClient()
        self.api.force_authenticate(boss)
        self.as_a = APIClient()
        self.as_a.force_authenticate(self.a.user)

    def _visit(self, employee: Employee, address: str = "г Киров, ул Ленина, 1", date: dt.date = DAY) -> TripStop:
        response = self.api.post("/api/visits/", {
            "address": address, "latitude": 58.6, "longitude": 49.66, "date": date.isoformat(),
            "window_start": "08:00", "window_end": "10:00", "employee_id": employee.pk,
        }, format="json")
        assert response.status_code == 201, response.data
        return TripStop.objects.get(pk=response.data["stop_id"])

    def test_only_management_sees_dispatch(self):
        for url in ("/api/dispatch/overview/", "/api/dispatch/map/", "/api/dispatch/schedule/", "/api/core/staff/"):
            assert self.as_a.get(url).status_code == 403, url
            assert self.api.get(url).status_code == 200, url

    def test_reassign_city_stop_moves_to_other_day_and_drops_empty(self):
        stop = self._visit(self.a)
        old_trip = stop.trip_id
        response = self.api.post("/api/dispatch/assign/", {
            "items": [{"type": "stop", "id": stop.pk}], "employee_id": self.b.pk,
        }, format="json")
        assert response.status_code == 200 and not response.data["errors"], response.data
        stop.refresh_from_db()
        assert stop.trip.lead == self.b and stop.trip.kind == TripKind.CITY and stop.planned_date == DAY
        assert stop.work_order.assigned_employee == self.b
        assert not Trip.objects.filter(pk=old_trip).exists()  # пустой автоматический день убран
        # и на другую дату
        self.api.post("/api/dispatch/assign/", {
            "items": [{"type": "stop", "id": stop.pk}], "employee_id": self.b.pk, "date": "2026-11-21",
        }, format="json")
        stop.refresh_from_db()
        assert stop.trip.date_start == dt.date(2026, 11, 21) and stop.work_order.scheduled_date == dt.date(2026, 11, 21)

    def test_trip_stop_assigns_inside_trip_and_adds_member(self):
        trip = Trip.objects.create(kind=TripKind.CLASSIC, title="Слободской", lead=self.a,
                                   date_start=DAY, date_end=DAY + dt.timedelta(days=2))
        response = self.api.post(f"/api/trips/{trip.pk}/stops/", {"address": "г Слободской, ул Советская, 1"}, format="json")
        assert response.status_code in (200, 201), response.data
        stop = TripStop.objects.get(trip=trip)
        result = self.api.post("/api/dispatch/assign/", {
            "items": [{"type": "stop", "id": stop.pk}], "employee_id": self.b.pk, "date": "2026-11-22",
        }, format="json").data
        assert not result["errors"], result
        stop.refresh_from_db()
        assert stop.trip_id == trip.pk and stop.work_order.assigned_employee == self.b
        assert trip.members.filter(pk=self.b.pk).exists()
        outside = self.api.post("/api/dispatch/assign/", {
            "items": [{"type": "stop", "id": stop.pk}], "employee_id": self.b.pk, "date": "2026-12-30",
        }, format="json").data
        assert outside["errors"] and "вне дат" in outside["errors"][0]["error"]

    def test_request_and_unscheduled_order_and_lab(self):
        req = Request.objects.create(contact_name="Иванова", contact_phone="+7900", address="г Киров, ул Мира, 2",
                                     latitude=58.6, longitude=49.6, consent_given=True)
        client = Client.objects.create(name="ООО Ромашка")
        site = Site.objects.create(client=client, address="г Киров, ул Мира, 5")
        field = WorkOrder.objects.create(client=client, site=site, assigned_employee=self.a)
        lab = WorkOrder.objects.create(client=client, site=site, assigned_employee=self.a, kind=WorkOrderKind.LAB)

        board = self.api.get("/api/dispatch/map/", {"date_from": DAY.isoformat()}).data
        assert {(q["type"], q["id"]) for q in board["queue"]} == {("request", req.pk), ("order", field.pk)}

        result = self.api.post("/api/dispatch/assign/", {
            "items": [{"type": "request", "id": req.pk}, {"type": "order", "id": field.pk},
                      {"type": "order", "id": lab.pk}],
            "employee_id": self.b.pk, "date": DAY.isoformat(), "time": "12:00",
        }, format="json").data
        assert not result["errors"], result
        req.refresh_from_db()
        field.refresh_from_db()
        lab.refresh_from_db()
        assert req.status == RequestStatus.CONFIRMED and req.assigned_employee == self.b
        assert TripStop.objects.get(work_order=field).trip.lead == self.b
        assert lab.assigned_employee == self.b
        board = self.api.get("/api/dispatch/map/", {"date_from": DAY.isoformat()}).data
        assert not board["queue"] and len(board["points"]) == 1 and len(board["unlocated"]) == 1

    def test_overview_and_schedule(self):
        self._visit(self.a)
        self._visit(self.a, "г Киров, ул Ленина, 3")
        EmployeeAvailability.objects.create(employee=self.b, date=DAY)
        data = self.api.get("/api/dispatch/overview/", {"date": DAY.isoformat()}).data
        cards = {c["id"]: c for c in data["employees"]}
        assert cards[self.a.pk]["state"] == "city" and cards[self.a.pk]["stops"]["total"] == 2
        assert cards[self.b.pk]["state"] == "free" and data["totals"]["stops_total"] == 2
        grid = self.api.get("/api/dispatch/schedule/", {"date_from": DAY.isoformat(), "date_to": DAY.isoformat()}).data
        rows = {r["id"]: r for r in grid["employees"]}
        assert rows[self.a.pk]["cells"][DAY.isoformat()]["stops"] == 2
        assert rows[self.b.pk]["cells"][DAY.isoformat()]["slots"][0]["start"] is None


class StaffTests(TestCase):
    def setUp(self) -> None:
        self.owner_user = User.objects.create_user(email="o@ei.test", password="pass12345", role=Role.OWNER)
        self.owner = Employee.objects.create(tab_number="01", full_name="Стариков Сергей Владимирович", user=self.owner_user)
        self.manager_user = User.objects.create_user(email="m@ei.test", password="pass12345", role=Role.MANAGER)
        Employee.objects.create(tab_number="02", full_name="Менеджер Мария", user=self.manager_user)
        self.api = APIClient()
        self.api.force_authenticate(self.owner_user)

    def test_create_with_login_and_temp_password(self):
        response = self.api.post("/api/core/staff/", {
            "full_name": "Новиков  Николай Ник.", "position": "Поверитель", "email": "N@ei.test", "role": "verifier",
        }, format="json")
        assert response.status_code == 201, response.data
        assert response.data["employee"]["tab_number"] == "03" and response.data["employee"]["email"] == "n@ei.test"
        user = User.objects.get(email="n@ei.test")
        assert user.check_password(response.data["password"]) and user.role == Role.VERIFIER
        assert user.employee.full_name == "Новиков Николай Ник."

    def test_update_role_deactivate_reset(self):
        e, _ = Employee.objects.get_or_create(tab_number="07", defaults={"full_name": "Без Входа Б."})
        # почта даёт вход и пароль
        r = self.api.patch(f"/api/core/staff/{e.pk}/", {"email": "x@ei.test", "role": "metrologist"}, format="json")
        assert r.status_code == 200 and r.data["password"] and r.data["employee"]["role"] == "metrologist"
        r = self.api.post(f"/api/core/staff/{e.pk}/password/")
        assert User.objects.get(email="x@ei.test").check_password(r.data["password"])
        r = self.api.patch(f"/api/core/staff/{e.pk}/", {"is_active": False}, format="json")
        assert r.status_code == 200 and not User.objects.get(email="x@ei.test").is_active
        assert self.api.patch(f"/api/core/staff/{self.owner.pk}/", {"is_active": False}, format="json").status_code == 400

    def test_manager_reads_but_does_not_edit(self):
        api = APIClient()
        api.force_authenticate(self.manager_user)
        assert api.get("/api/core/staff/").data["can_admin"] is False
        assert api.post("/api/core/staff/", {"full_name": "Кто-то Новый"}, format="json").status_code == 400
        head = User.objects.create_user(email="h@ei.test", password="pass12345", role=Role.HEAD)
        api.force_authenticate(head)
        assert api.patch(f"/api/core/staff/{self.owner.pk}/", {"role": "verifier"}, format="json").status_code == 400
        assert api.post("/api/core/staff/", {"full_name": "Ещё Один", "role": "owner", "email": "e@ei.test"},
                        format="json").status_code == 400
