"""Командировки: маршрут, спрос, раскладка по дням, брони эталонов, план/факт, пакет."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import District, Standard
from apps.core.models import Role, User
from apps.logistics import routing, service
from apps.logistics.models import StandardReservation, StopStatus, Trip, TripStop
from apps.verification.models import Client, Site, WorkOrder, WorkOrderStatus
from apps.verification.render import typst_available
from apps.verification.tests import factories as f

KIROV = (58.6036, 49.6680)


class RoutingTestCase(TestCase):
    def test_haversine_is_sane(self):
        # Киров — Котельнич ≈ 87 км по прямой
        assert 80 < routing.haversine_km(KIROV, (58.3070, 48.3460)) < 95

    def test_two_opt_untangles_a_crossed_route(self):
        # Точки на прямой: депо, 3, 1, 2 — правильный порядок 1, 2, 3
        points = [routing.Point(name, 58.60, lon) for name, lon in (("a", 49.70), ("b", 49.74), ("c", 49.68))]
        route = routing.plan_route((58.60, 49.66), points)
        assert [points[i].key for i in route.order] == ["c", "a", "b"]
        assert route.source == "straight"
        assert route.minutes == sorted(route.minutes)

    def test_late_window_is_reported(self):
        far = routing.Point("далеко", 58.30, 48.35, window_to=dt.time(9, 10))
        route = routing.plan_route(KIROV, [far])
        assert any("позже окна" in w for w in route.warnings)

    def test_early_arrival_waits_for_the_window(self):
        near = routing.Point("рядом", 58.604, 49.669, window_from=dt.time(11, 0))
        route = routing.plan_route(KIROV, [near], day_start=dt.time(9, 0))
        assert route.minutes[0] == 120

    @override_settings(LOGISTICS_OSRM_URL="http://osrm:5000")
    def test_osrm_failure_falls_back_to_straight(self):
        with mock.patch("apps.logistics.routing.requests.get", side_effect=OSError("нет сети")):
            route = routing.plan_route(KIROV, [routing.Point("x", 58.61, 49.70)])
        assert route.source == "straight"

    @override_settings(LOGISTICS_OSRM_URL="http://osrm:5000")
    def test_osrm_matrix_is_used(self):
        fake = mock.Mock()
        fake.json.return_value = {"code": "Ok", "durations": [[0, 600], [600, 0]], "distances": [[0, 5000], [5000, 0]]}
        with mock.patch("apps.logistics.routing.requests.get", return_value=fake):
            route = routing.plan_route(KIROV, [routing.Point("x", 58.61, 49.70, service_minutes=0)])
        assert route.source == "osrm" and route.minutes == [10.0] and route.total_km == 5.0


@override_settings(LOGISTICS_DEPOT_LAT=str(KIROV[0]), LOGISTICS_DEPOT_LON=str(KIROV[1]))
class TripTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        self.district = District.objects.create(name="Слободской район")
        self.client_ = Client.objects.create(name="ТСЖ Луч")
        self.trip = Trip.objects.create(title="Слободской", date_from=dt.date(2026, 12, 1),
                                        date_to=dt.date(2026, 12, 2))

    def work_order(self, lat, lon, *, restricted=False, time=None, district=None):
        site = Site.objects.create(client=self.client_, address=f"дом {lat},{lon}", latitude=lat, longitude=lon,
                                   district=district or self.district, is_restricted=restricted)
        return WorkOrder.objects.create(client=self.client_, site=site, assigned_employee=self.employee,
                                        scheduled_time=time)

    def test_demand_groups_open_work_orders_and_expiring_instruments(self):
        self.work_order(58.72, 50.18)
        v = f.make_verification(self.family, self.employee, day=5)
        v.status = "accepted"
        v.next_verification_date = dt.date.today() + dt.timedelta(days=10)
        v.save()
        groups = service.demand(30)
        names = {g["district"]: g for g in groups}
        assert names["Слободской район"]["work_orders"] == 1
        assert names["Район не определён"]["expiring"] == 1

    def test_optimize_splits_days_by_capacity_and_orders_stops(self):
        ids = [self.work_order(58.72 + i * 0.01, 50.18).pk for i in range(6)]
        service.add_work_orders(self.trip, ids)
        assert list(self.trip.employees.all()) == [self.employee]
        with override_settings(LOGISTICS_DAY_MINUTES=200):
            summary = service.optimize(self.trip)
        days = {s.day for s in self.trip.stops.all()}
        assert days == {dt.date(2026, 12, 1), dt.date(2026, 12, 2)}
        assert sum(d["stops"] for d in summary["days"]) == 6
        first_day = list(self.trip.stops.filter(day=dt.date(2026, 12, 1)).order_by("position"))
        assert [s.position for s in first_day] == list(range(1, len(first_day) + 1))
        assert all(s.eta for s in first_day)

    def test_stop_without_coordinates_is_kept_but_reported(self):
        wo = self.work_order(None, None)
        service.add_work_orders(self.trip, [wo.pk, self.work_order(58.7, 50.1).pk])
        summary = service.optimize(self.trip)
        assert summary["unlocated"] == [wo.site.address]

    def test_work_order_is_only_in_one_trip(self):
        wo = self.work_order(58.7, 50.1)
        service.add_work_orders(self.trip, [wo.pk])
        other = Trip.objects.create(title="Ещё", date_from=dt.date(2026, 12, 5), date_to=dt.date(2026, 12, 5))
        assert service.add_work_orders(other, [wo.pk]) == []

    def test_standard_reservation_refuses_overlap_and_expired_standard(self):
        standard = Standard.objects.create(fif_number="3.1.ААА.1", name="Установка", verified_at=dt.date(2026, 1, 1),
                                           valid_until=dt.date(2027, 1, 1))
        old = Standard.objects.create(fif_number="3.1.ААА.2", name="Старая", verified_at=dt.date(2025, 1, 1),
                                      valid_until=dt.date(2026, 12, 1))
        assert service.reserve(self.trip, [standard.pk]) == []
        other = Trip.objects.create(title="Пересекается", date_from=dt.date(2026, 12, 2), date_to=dt.date(2026, 12, 3))
        refused = service.reserve(other, [standard.pk, old.pk])
        assert len(refused) == 2
        assert "уже забронирован" in refused[0]["detail"] or "уже забронирован" in refused[1]["detail"]
        assert StandardReservation.objects.count() == 1

    def test_plan_fact_follows_work_orders(self):
        a, b = self.work_order(58.7, 50.1), self.work_order(58.71, 50.1)
        service.add_work_orders(self.trip, [a.pk, b.pk])
        a.status = WorkOrderStatus.DONE
        a.save()
        self.trip.actual_cost = Decimal("1000")
        self.trip.save()
        service.refresh_stop_statuses(self.trip)
        fact = service.plan_fact(self.trip)
        assert fact["stops_done"] == 1 and fact["stops_left"] == 1
        assert TripStop.objects.get(work_order=a).status == StopStatus.DONE

    @pytest.mark.skipif(not typst_available(), reason="нет typst")
    def test_pack_has_route_sheet_and_blanks_for_restricted_sites(self):
        from pypdf import PdfReader
        import io

        service.add_work_orders(self.trip, [self.work_order(58.7, 50.1, restricted=True).pk,
                                            self.work_order(58.71, 50.1).pk])
        service.optimize(self.trip)
        pdf = service.render_pack(self.trip)
        reader = PdfReader(io.BytesIO(pdf))
        assert "Маршрутный лист" in reader.pages[0].extract_text()
        assert len(reader.pages) >= 3  # лист + два экземпляра бланка

    def test_api_permissions_and_flow(self):
        api = APIClient()
        verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        api.force_authenticate(verifier)
        assert api.post(reverse("logistics_trips"), {}, format="json").status_code == 403
        assert api.get(reverse("logistics_trips")).json() == []

        api.force_authenticate(User.objects.create_user("m@ei.test", "pw", role=Role.MANAGER))
        wo = self.work_order(58.7, 50.1)
        created = api.post(reverse("logistics_trips"), {
            "title": "Кирово-Чепецк", "date_from": "2026-12-10", "date_to": "2026-12-10",
            "work_order_ids": [wo.pk],
        }, format="json")
        assert created.status_code == 201, created.content
        trip_id = created.json()["id"]
        optimized = api.post(reverse("logistics_trip_optimize", args=[trip_id]))
        assert optimized.status_code == 200
        assert optimized.json()["trip"]["stops"][0]["eta"]
        demand = api.get(reverse("logistics_demand")).json()
        assert "groups" in demand

        # поверитель командировки видит её и отмечает остановку
        verifier.employee = self.employee
        self.employee.user = verifier
        self.employee.save()
        api.force_authenticate(verifier)
        assert len(api.get(reverse("logistics_trips")).json()) == 1
        stop_id = optimized.json()["trip"]["stops"][0]["id"]
        marked = api.patch(reverse("logistics_stop", args=[stop_id]), {"status": "done"}, format="json")
        assert marked.status_code == 200 and marked.json()["status"] == "done"
