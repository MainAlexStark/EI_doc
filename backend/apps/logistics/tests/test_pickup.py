"""Режим «Город»: забрать коробку с предприятия → приёмка → вернуть приборы."""

from __future__ import annotations

import datetime as dt

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.logistics.models import StopPurpose, StopStatus, TripKind, TripStop
from apps.verification.models import Client, ClientKind, WorkOrder, WorkOrderKind, WorkOrderStatus


class PickupTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        boss = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        self.manager = Employee.objects.create(tab_number="01", full_name="Менеджер М. М.", user=boss)
        user = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)
        self.verifier = Employee.objects.create(tab_number="05", full_name="Кузнецов Иван", user=user)
        self.boss_api, self.api = APIClient(), APIClient()
        self.boss_api.force_authenticate(boss)
        self.api.force_authenticate(user)
        self.day = dt.date.today() + dt.timedelta(days=1)
        self.client_org = Client.objects.create(kind=ClientKind.ORG, name="АО «Кировский шинный»", inn="4345000000",
                                                legal_address="г Киров, ул Луганская, 51")

    def pickup(self, **over):
        body = {"client_id": self.client_org.pk, "contact_name": "Петров, главный метролог",
                "contact_phone": "+7 (912) 000-00-00", "planned_count": 25, "date": self.day.isoformat(),
                "window_start": "10:00", "window_end": "12:00", "employee_id": self.verifier.pk}
        body.update(over)
        return self.boss_api.post("/api/pickups/", body, format="json")

    def test_pickup_lands_in_the_city_day_with_legal_address(self):
        r = self.pickup()
        assert r.status_code == 201, r.data
        assert r.data["purpose"] == "pickup" and r.data["address"] == "г Киров, ул Луганская, 51"
        assert r.data["planned_count"] == 25 and r.data["window_start"] == "10:00"
        stop = TripStop.objects.get(pk=r.data["id"])
        assert stop.trip.kind == TripKind.CITY and stop.trip.lead == self.verifier
        assert stop.client == self.client_org and stop.work_order.assigned_employee == self.verifier

    def test_new_client_by_inn_and_name(self):
        r = self.pickup(client_id=None, inn="4345111111", name="ООО «Новый завод»", address="г Киров, ул Мира, 1")
        assert r.status_code == 201, r.data
        assert Client.objects.get(inn="4345111111").kind == ClientKind.ORG

    def test_verifier_cannot_assign_someone_else(self):
        body = {"client_id": self.client_org.pk, "date": self.day.isoformat(), "employee_id": self.manager.pk}
        assert self.api.post("/api/pickups/", body, format="json").status_code == 403
        body.pop("employee_id")
        assert self.api.post("/api/pickups/", body, format="json").status_code == 201

    def test_picked_up_creates_intake_once(self):
        stop_id = self.pickup().data["id"]
        r = self.api.post(f"/api/trip-stops/{stop_id}/pickup/", {"count": 23}, format="json")
        assert r.status_code == 201, r.data
        order = WorkOrder.objects.get(pk=r.data["order"]["id"])
        assert order.kind == WorkOrderKind.LAB and order.client == self.client_org and order.received_at
        assert order.assigned_employee == self.verifier and "Привезено с выезда" in order.note
        assert r.data["order"]["pickup"]["stop_id"] == stop_id
        stop = TripStop.objects.get(pk=stop_id)
        assert stop.status == StopStatus.DONE and stop.planned_count == 23 and stop.lab_order == order
        assert stop.work_order.status == WorkOrderStatus.DONE  # выезд-забор закрыт без поверок
        again = self.api.post(f"/api/trip-stops/{stop_id}/pickup/", {}, format="json")
        assert again.status_code == 200 and again.data["order"]["id"] == order.pk
        assert WorkOrder.objects.filter(kind=WorkOrderKind.LAB).count() == 1

    def test_ordinary_stop_cannot_be_picked_up(self):
        stop = TripStop.objects.get(pk=self.pickup().data["id"])
        TripStop.objects.filter(pk=stop.pk).update(purpose=StopPurpose.VISIT)
        assert self.api.post(f"/api/trip-stops/{stop.pk}/pickup/", {}, format="json").status_code == 400

    def test_delivery_back_marks_order_issued(self):
        stop_id = self.pickup().data["id"]
        order_id = self.api.post(f"/api/trip-stops/{stop_id}/pickup/", {}, format="json").data["order"]["id"]
        later = (self.day + dt.timedelta(days=3)).isoformat()
        r = self.boss_api.post(f"/api/orders/{order_id}/delivery/", {"date": later, "employee_id": self.verifier.pk},
                               format="json")
        assert r.status_code == 201, r.data
        assert r.data["purpose"] == "delivery" and r.data["lab_order_id"] == order_id
        assert r.data["address"] == "г Киров, ул Луганская, 51" and r.data["contact_name"].startswith("Петров")
        repeat = self.boss_api.post(f"/api/orders/{order_id}/delivery/", {"date": later}, format="json")
        assert repeat.status_code == 200 and repeat.data["id"] == r.data["id"]

        listed = self.boss_api.get("/api/work-orders/", {"kind": "lab"}).data
        assert listed[0]["delivery"]["stop_id"] == r.data["id"] and listed[0]["pickup"]["stop_id"] == stop_id

        done = self.api.patch(f"/api/trip-stops/{r.data['id']}/", {"status": "done"}, format="json")
        assert done.status_code == 200
        assert WorkOrder.objects.get(pk=order_id).issued_at is not None
        again = self.boss_api.post(f"/api/orders/{order_id}/delivery/", {"date": later}, format="json")
        assert again.status_code == 400  # уже выдано
