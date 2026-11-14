"""Маршрут дня: порядок по времени в пути, окна клиентов, пробки 2ГИС, день поверителя из заявки."""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.logistics import matrix, planner, service
from apps.logistics.models import StopStatus, Trip, TripKind, TripStop
from apps.verification.models import Client, Site, WorkOrder, WorkOrderKind

# Киров: офис на Красноармейской и адреса по линии на восток, ~1 км друг от друга.
BASE = (58.6000, 49.6000)


def east(km: float) -> tuple[float, float]:
    return BASE[0], BASE[1] + km / 58.0  # на широте Кирова градус долготы ≈ 58 км


class PlannerBase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        user = User.objects.create_user(email="lead@ei.test", password="pass12345", role=Role.OWNER)
        self.lead = Employee.objects.create(tab_number="02", full_name="Стариков А. С.", user=user)
        self.api = APIClient()
        self.api.force_authenticate(user)
        self.trip = Trip.objects.create(title="Киров — 16 ноября", kind=TripKind.CITY, lead=self.lead,
                                        date_start=dt.date(2026, 11, 16))
        self.departure = timezone.make_aware(dt.datetime(2026, 11, 16, 9, 0))

    def stop(self, km: float, *, position: int, window=None, count=None, status=StopStatus.PLANNED) -> TripStop:
        client = Client.objects.create(name=f"Клиент {km}")
        lat, lon = east(km)
        site = Site.objects.create(client=client, address=f"ул Тестовая, {km}", latitude=round(lat, 6), longitude=round(lon, 6))
        return TripStop.objects.create(
            trip=self.trip, position=position, site=site, client=client, status=status, planned_count=count,
            window_start=window[0] if window else None, window_end=window[1] if window else None,
        )

    def options(self, **extra) -> planner.PlanOptions:
        return planner.PlanOptions(start="me", me=BASE, departure=self.departure, service_minutes=15,
                                   per_instrument_minutes=0, **extra)


class PlannerTests(PlannerBase):
    def test_visits_nearest_first_and_saves_eta(self):
        far = self.stop(6, position=1)
        near = self.stop(1, position=2)
        middle = self.stop(3, position=3)
        result = planner.plan(self.trip, self.options(), apply=True)
        assert [row["id"] for row in result["rows"]] == [near.pk, middle.pk, far.pk]
        summary = result["summary"]
        assert summary["provider"] == "estimate" and summary["traffic"] is False
        assert summary["stops"] == 3 and summary["km"] > 7
        etas = [dt.datetime.fromisoformat(row["eta"]) for row in result["rows"]]
        assert etas == sorted(etas) and etas[0] > self.departure
        # сохранено: порядок, время приезда, сводка
        saved = list(self.trip.stops.order_by("position").values_list("pk", flat=True))
        assert saved == [near.pk, middle.pk, far.pk]
        assert TripStop.objects.get(pk=near.pk).eta is not None
        self.trip.refresh_from_db()
        assert self.trip.route["stops"] == 3

    def test_preview_does_not_touch_anything(self):
        a, b = self.stop(5, position=1), self.stop(1, position=2)
        planner.plan(self.trip, self.options())
        assert list(self.trip.stops.order_by("position").values_list("pk", flat=True)) == [a.pk, b.pk]
        assert not TripStop.objects.filter(eta__isnull=False).exists()

    def test_promised_time_beats_distance(self):
        # Ближний адрес ждут только после обеда — сначала едем к дальнему.
        near = self.stop(1, position=1, window=(dt.time(14, 0), dt.time(15, 0)))
        far = self.stop(4, position=2)
        result = planner.plan(self.trip, self.options())
        assert [row["id"] for row in result["rows"]] == [far.pk, near.pk]
        near_row = result["rows"][1]
        assert dt.datetime.fromisoformat(near_row["eta"]).hour == 14 and near_row["wait_minutes"] > 0

    def test_impossible_window_is_reported_not_refused(self):
        self.stop(30, position=1, window=(dt.time(9, 0), dt.time(9, 5)))
        result = planner.plan(self.trip, self.options())
        assert result["summary"]["late"] == 1 and result["rows"][0]["late_minutes"] > 30

    def test_service_time_grows_with_instruments_and_day_end_is_flagged(self):
        self.stop(1, position=1, count=40)
        self.stop(2, position=2)
        result = planner.plan(self.trip, planner.PlanOptions(
            start="me", me=BASE, departure=self.departure, service_minutes=15, per_instrument_minutes=10,
            day_end=dt.time(12, 0)))
        last = result["rows"][-1]
        assert last["after_day_end"] is True and result["summary"]["after_day_end"] == 2  # 6,5 часа на первом адресе

    def test_done_and_unlocated_stops_keep_out_of_the_route(self):
        done = self.stop(2, position=1, status=StopStatus.DONE)
        blind_client = Client.objects.create(name="Без точки")
        blind = TripStop.objects.create(trip=self.trip, position=2, client=blind_client,
                                        site=Site.objects.create(client=blind_client, address="где-то"))
        live = self.stop(3, position=3)
        result = planner.plan(self.trip, self.options(), apply=True)
        assert [row["id"] for row in result["rows"]] == [live.pk]
        assert "без точки" in " ".join(result["summary"]["notes"])
        order = list(self.trip.stops.order_by("position").values_list("pk", flat=True))
        assert order == [done.pk, live.pk, blind.pk]

    def test_start_from_first_address(self):
        first = self.stop(5, position=1)
        other = self.stop(6, position=2)
        result = planner.plan(self.trip, planner.PlanOptions(start="first", departure=self.departure))
        assert [row["id"] for row in result["rows"]] == [first.pk, other.pk]
        assert result["rows"][0]["eta"] == self.departure.astimezone(timezone.get_current_timezone()).isoformat()

    def test_base_without_address_is_explained(self):
        self.stop(1, position=1)
        response = self.api.post(f"/api/trips/{self.trip.pk}/plan/", {"start": "base"}, format="json")
        assert response.status_code == 400 and "офис" in str(response.data)

    def test_api_preview_and_apply(self):
        far, near = self.stop(4, position=1), self.stop(1, position=2)
        url = f"/api/trips/{self.trip.pk}/plan/"
        body = {"start": "me", "latitude": BASE[0], "longitude": BASE[1], "departure": self.departure.isoformat()}
        preview = self.api.post(url, body, format="json")
        assert preview.status_code == 200, preview.data
        assert [row["id"] for row in preview.data["rows"]] == [near.pk, far.pk] and "trip" not in preview.data
        applied = self.api.post(url, {**body, "apply": True}, format="json")
        stops = applied.data["trip"]["stops"]
        assert [stop["id"] for stop in stops] == [near.pk, far.pk]
        assert stops[0]["eta"] and stops[0]["drive_minutes"] is not None
        assert applied.data["trip"]["route"]["provider_label"].startswith("оценка")

    def test_nothing_to_plan(self):
        response = self.api.post(f"/api/trips/{self.trip.pk}/plan/", {"start": "me", "latitude": 58.6, "longitude": 49.6},
                                 format="json")
        assert response.status_code == 400


class MultiDayTests(PlannerBase):
    def test_addresses_that_do_not_fit_move_to_the_next_working_day(self):
        self.trip.kind = TripKind.CLASSIC
        self.trip.date_end = dt.date(2026, 11, 17)
        self.trip.save()
        stops = [self.stop(km, position=i + 1, count=1) for i, km in enumerate((1, 2, 3, 4, 5, 6))]
        # По 2 часа на адрес, выезд в 9:00, день до 13:30: в первый день влезают два адреса.
        options = planner.PlanOptions(start="me", me=BASE, departure=self.departure, service_minutes=120,
                                      per_instrument_minutes=0, day_start=dt.time(8, 0), day_end=dt.time(13, 30),
                                      last_day=dt.date(2026, 11, 17))
        result = planner.plan(self.trip, options, apply=True)
        days = [row["day"] for row in result["rows"]]
        assert days[:2] == ["2026-11-16"] * 2 and days[2:4] == ["2026-11-17"] * 2
        assert result["summary"]["days"] == ["2026-11-16", "2026-11-17"] and result["summary"]["multi_day"]
        morning = dt.datetime.fromisoformat(result["rows"][2]["eta"])
        assert (morning.date(), morning.hour) == (dt.date(2026, 11, 17), 8)  # утром от места ночёвки, без дороги из Кирова
        # дальше дней нет — последние адреса помечены «после конца дня», но не потеряны
        assert all(row["after_day_end"] for row in result["rows"][4:])
        third = TripStop.objects.get(pk=result["rows"][2]["id"])
        assert third.planned_date == dt.date(2026, 11, 17)
        assert len(stops) == len(result["rows"])

    def test_api_spreads_over_trip_days_by_default(self):
        self.trip.date_end = dt.date(2026, 11, 18)
        self.trip.save()
        for i in range(8):
            self.stop(i + 1, position=i + 1)
        body = {"start": "me", "latitude": BASE[0], "longitude": BASE[1], "departure": self.departure.isoformat(),
                "service_minutes": 90}
        spread = self.api.post(f"/api/trips/{self.trip.pk}/plan/", body, format="json").data
        assert len(spread["summary"]["days"]) == 2 and spread["summary"]["day_end"] == "17:00"
        one_day = self.api.post(f"/api/trips/{self.trip.pk}/plan/", {**body, "multi_day": False}, format="json").data
        assert one_day["summary"]["days"] == ["2026-11-16"] and one_day["summary"]["after_day_end"] > 0


class DgisMatrixTests(PlannerBase):
    def reply(self, status_code=200, routes=None):
        return mock.Mock(status_code=status_code, text="", json=mock.Mock(return_value={"routes": routes or []}))

    @override_settings(DGIS_API_KEY="demo")
    def test_traffic_matrix_is_used_and_cached(self):
        points = [BASE, east(1), east(2)]
        routes = [
            {"source_id": i, "target_id": j, "status": "OK", "duration": 600 + 60 * (i + j), "distance": 1000 * abs(i - j)}
            for i in range(3) for j in range(3) if i != j
        ]
        with mock.patch("apps.logistics.matrix.requests.post", return_value=self.reply(routes=routes)) as post:
            first = matrix.build(points, self.departure)
            second = matrix.build(points, self.departure)
        post.assert_called_once()  # второй раз — из кэша
        body = post.call_args.kwargs["json"]
        assert post.call_args.kwargs["params"] == {"key": "demo", "version": "2.0"}
        assert body["points"][1] == {"lat": east(1)[0], "lon": east(1)[1]}
        assert body["type"] == "statistics" and body["start_time"].endswith("Z")
        assert first.provider == "2gis" and first.traffic and first.seconds[0][2] == 720
        assert second.seconds == first.seconds

    @override_settings(DGIS_API_KEY="demo")
    def test_rejected_start_time_falls_back_to_current_traffic(self):
        ok = self.reply(routes=[{"source_id": 0, "target_id": 1, "status": "OK", "duration": 300, "distance": 900},
                                {"source_id": 1, "target_id": 0, "status": "OK", "duration": 320, "distance": 900}])
        with mock.patch("apps.logistics.matrix.requests.post", side_effect=[self.reply(400), ok]) as post:
            result = matrix.build([BASE, east(1)], self.departure)
        assert "start_time" not in post.call_args_list[1].kwargs["json"]
        assert result.seconds[0][1] == 300 and result.label == "2ГИС, текущие пробки"

    @override_settings(DGIS_API_KEY="demo")
    def test_missing_pairs_are_estimated_and_outage_is_not_fatal(self):
        with mock.patch("apps.logistics.matrix.requests.post", return_value=self.reply(routes=[])):
            partial = matrix.build([BASE, east(1)], self.departure)
        assert partial.seconds[0][1] > 0 and "не нашёл дорогу" in partial.notes[0]
        cache.clear()
        import requests

        with mock.patch("apps.logistics.matrix.requests.post", side_effect=requests.ConnectionError("нет")):
            down = matrix.build([BASE, east(1)], self.departure)
        assert down.provider == "estimate" and "не ответил" in down.notes[0]

    @override_settings(DGIS_API_KEY="demo")
    def test_big_day_is_split_into_blocks(self):
        points = [east(i * 0.3) for i in range(30)]
        with mock.patch("apps.logistics.matrix.requests.post", return_value=self.reply(routes=[])) as post:
            matrix.build(points, self.departure)
        assert post.call_count == 4  # 30 точек → блоки 25 + 5 по отправлению и назначению
        assert all(len(call.kwargs["json"]["sources"]) <= 25 for call in post.call_args_list)


class CityDayTests(TestCase):
    def setUp(self) -> None:
        self.employee = Employee.objects.create(tab_number="05", full_name="Кузнецов И. П.")
        self.client_obj = Client.objects.create(name="Иванова М. П.", phone="+79000000001")

    def order(self, **extra) -> WorkOrder:
        site = Site.objects.create(client=self.client_obj, address=f"Киров, ул Ленина, {WorkOrder.objects.count() + 1}")
        return WorkOrder.objects.create(client=self.client_obj, site=site, assigned_employee=self.employee, **extra)

    def test_dated_order_goes_into_the_verifier_day_with_a_window(self):
        first = service.attach_to_city_day(self.order(scheduled_date=dt.date(2026, 11, 16), scheduled_time=dt.time(10, 30)))
        second = service.attach_to_city_day(self.order(scheduled_date=dt.date(2026, 11, 16)))
        other_day = service.attach_to_city_day(self.order(scheduled_date=dt.date(2026, 11, 17)))
        assert first.trip == second.trip != other_day.trip
        assert first.trip.kind == TripKind.CITY and first.trip.title == "Киров — 16 ноября"
        assert (first.window_start, first.window_end) == (dt.time(10, 30), dt.time(12, 30))
        assert second.window_start is None and second.position == 2
        assert first.contact_phone == "+79000000001"

    def test_undated_lab_or_already_placed_orders_are_left_alone(self):
        assert service.attach_to_city_day(self.order()) is None
        assert service.attach_to_city_day(self.order(scheduled_date=dt.date(2026, 11, 16), kind=WorkOrderKind.LAB)) is None
        placed = self.order(scheduled_date=dt.date(2026, 11, 16))
        service.attach_to_city_day(placed)
        assert service.attach_to_city_day(placed) is None
        assert TripStop.objects.count() == 1

    def test_confirmed_request_lands_in_the_day(self):
        from apps.hub.models import Request
        from apps.hub.service import confirm_request

        request = Request.objects.create(contact_name="Петров", contact_phone="+79000000002", address="Киров, ул Мира, 1",
                                         desired_date=dt.date(2026, 11, 18), desired_time=dt.time(9, 0), comment="Домофон 12")
        order = confirm_request(request, employee=self.employee)
        stop = TripStop.objects.get(work_order=order)
        assert stop.trip.date_start == dt.date(2026, 11, 18) and stop.trip.lead == self.employee
        assert stop.window_start == dt.time(9, 0) and stop.note == "Домофон 12"
