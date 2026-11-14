"""Командировки: адреса из плана и на месте, отметки, заходы по закупке, права."""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import uuid
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Employee, Role, User
from apps.logistics import geocode, service
from apps.logistics.models import (
    Pass,
    PassStatus,
    Procurement,
    StopStatus,
    Trip,
    TripStatus,
    TripStop,
)
from apps.verification.models import (
    Client,
    ClientKind,
    Instrument,
    Site,
    Verification,
    VerificationStatus,
    WorkOrder,
    WorkOrderStatus,
)


def make_user(email: str, role: str, tab: str, name: str) -> tuple[User, Employee]:
    user = User.objects.create_user(email=email, password="pass12345", role=role)
    return user, Employee.objects.create(tab_number=tab, full_name=name, user=user)


def _body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


class Base(TestCase):
    def setUp(self) -> None:
        self.owner_user, self.owner = make_user("owner@ei.test", Role.OWNER, "01", "Стариков А. С.")
        self.verifier_user, self.verifier = make_user("v@ei.test", Role.VERIFIER, "02", "Поверитель П. П.")
        self.other_user, self.other = make_user("o@ei.test", Role.VERIFIER, "03", "Чужой Ч. Ч.")
        self.api = APIClient()
        self.api.force_authenticate(self.owner_user)

    def as_user(self, user: User) -> APIClient:
        api = APIClient()
        api.force_authenticate(user)
        return api

    def make_trip(self, **extra) -> dict:
        payload = {"title": "Слободской район", "destination": "Слободской", "date_start": "2026-11-14",
                   "date_end": "2026-11-15", **extra}
        response = self.api.post("/api/trips/", payload, format="json")
        assert response.status_code == 201, response.data
        return response.data

    def add_verification(self, work_order_id: int, serial: str = "A100", *, status=VerificationStatus.READY,
                         suitable: bool = True) -> Verification:
        family, _ = MeasurementFamily.objects.get_or_create(code="water", defaults={"name": "Счётчики воды"})
        si_type, _ = SiType.objects.get_or_create(
            registry_number="12345-20", defaults={"name": "СВ-15", "family": family}
        )
        order = WorkOrder.objects.get(pk=work_order_id)
        instrument = Instrument.objects.create(si_type=si_type, serial_number=serial, owner=order.client, site=order.site)
        return Verification.objects.create(
            client_id=uuid.uuid4(), work_order=order, instrument=instrument, verifier=self.owner,
            verified_at=timezone.now(), status=status, suitable=suitable,
        )


class ClassicTripTests(Base):
    def test_create_defaults_to_me_as_lead(self):
        trip = self.make_trip(member_ids=[self.verifier.pk, self.owner.pk])
        assert trip["kind"] == "classic"
        assert trip["lead"]["id"] == self.owner.pk
        assert [m["id"] for m in trip["members"]] == [self.verifier.pk]  # старший в участниках не дублируется
        assert trip["stops"] == [] and trip["summary"]["stops_total"] == 0

    def test_dates_are_checked(self):
        response = self.api.post("/api/trips/", {"title": "x", "date_start": "2026-11-15", "date_end": "2026-11-14"},
                                 format="json")
        assert response.status_code == 400

    def test_planned_stop_creates_client_site_and_work_order(self):
        trip = self.make_trip()
        response = self.api.post(f"/api/trips/{trip['id']}/stops/", {
            "address": "г Слободской, ул Советская, д 10, кв 5", "client_name": "Иванова М. П.",
            "contact_phone": "+79000000001", "planned_count": 2, "latitude": 58.7312, "longitude": 50.1668,
        }, format="json")
        assert response.status_code == 201, response.data
        stop = response.data
        assert stop["status"] == "planned" and stop["position"] == 1 and not stop["added_on_site"]
        assert stop["latitude"] == 58.7312
        order = WorkOrder.objects.get(pk=stop["work_order_id"])
        assert order.assigned_employee == self.owner
        assert order.scheduled_date == dt.date(2026, 11, 14)
        assert order.client.name == "Иванова М. П." and order.client.kind == ClientKind.PERSON
        assert order.site.address == "г Слободской, ул Советская, д 10, кв 5"

    def test_on_site_stop_goes_to_whoever_added_it_and_is_dated_today(self):
        trip = self.make_trip(member_ids=[self.verifier.pk])
        api = self.as_user(self.verifier_user)
        response = api.post(f"/api/trips/{trip['id']}/stops/", {"address": "д Стулово, ул Мира, 3", "added_on_site": True},
                            format="json")
        assert response.status_code == 201, response.data
        order = WorkOrder.objects.get(pk=response.data["work_order_id"])
        assert order.assigned_employee == self.verifier
        assert order.scheduled_date == timezone.localdate()
        assert order.client.name == service.ANONYMOUS_CLIENT
        assert response.data["added_on_site"] is True

    def test_anonymous_clients_share_one_record(self):
        trip = self.make_trip()
        for address in ("ул Ленина, 1", "ул Ленина, 2"):
            assert self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": address}, format="json").status_code == 201
        assert Client.objects.filter(name=service.ANONYMOUS_CLIENT).count() == 1
        assert Site.objects.count() == 2

    def test_same_address_twice_is_refused(self):
        trip = self.make_trip()
        url = f"/api/trips/{trip['id']}/stops/"
        assert self.api.post(url, {"address": "ул Ленина, 1"}, format="json").status_code == 201
        again = self.api.post(url, {"address": "ул  ленина, 1"}, format="json")
        assert again.status_code == 400
        assert "уже есть" in str(again.data)

    def test_bulk_lines(self):
        trip = self.make_trip()
        text = "ул Ленина, 1; Петров; +7 900 000-00-02; 3\n\nул Ленина, 2\nул Ленина, 1\n"
        response = self.api.post(f"/api/trips/{trip['id']}/stops/bulk/", {"text": text}, format="json")
        assert response.status_code == 200, response.data
        assert response.data["added"] == 2 and len(response.data["skipped"]) == 1
        first = response.data["trip"]["stops"][0]
        assert first["client"] == "Петров" and first["contact_phone"] == "+7 900 000-00-02" and first["planned_count"] == 3

    def test_marks_and_summary(self):
        trip = self.make_trip()
        url = f"/api/trips/{trip['id']}/stops/"
        a = self.api.post(url, {"address": "ул Ленина, 1", "planned_count": 2}, format="json").data
        b = self.api.post(url, {"address": "ул Ленина, 2"}, format="json").data
        c = self.api.post(url, {"address": "ул Ленина, 3", "added_on_site": True}, format="json").data
        self.add_verification(a["work_order_id"], "A1")
        self.add_verification(a["work_order_id"], "A2", suitable=False)
        self.add_verification(c["work_order_id"], "A3", status=VerificationStatus.DRAFT)

        assert self.api.patch(f"/api/trip-stops/{a['id']}/", {"status": "done"}, format="json").status_code == 200
        marked = self.api.patch(f"/api/trip-stops/{b['id']}/", {"status": "no_answer"}, format="json")
        assert marked.data["status"] == "no_answer" and marked.data["visited_at"]
        # Пустой наряд несостоявшегося визита закрывается, чтобы не висел в «Поверках».
        assert WorkOrder.objects.get(pk=b["work_order_id"]).status == WorkOrderStatus.CANCELLED

        data = self.api.get(f"/api/trips/{trip['id']}/").data
        assert data["status"] == TripStatus.ACTIVE  # первая отметка начинает командировку
        summary = data["summary"]
        assert summary["stops_total"] == 3 and summary["stops_done"] == 1 and summary["stops_no_answer"] == 1
        assert summary["stops_on_site"] == 1 and summary["instruments_planned"] == 2
        assert summary["instruments_entered"] == 3 and summary["instruments_ready"] == 2
        assert summary["instruments_unsuitable"] == 1
        assert data["stops"][0]["verifications_total"] == 2

        # Вернули «ждёт» — наряд снова открыт.
        self.api.patch(f"/api/trip-stops/{b['id']}/", {"status": "planned"}, format="json")
        assert WorkOrder.objects.get(pk=b["work_order_id"]).status == WorkOrderStatus.PLANNED

    def test_list_carries_progress(self):
        trip = self.make_trip()
        stop = self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 1"}, format="json").data
        self.add_verification(stop["work_order_id"])
        self.api.patch(f"/api/trip-stops/{stop['id']}/", {"status": "done"}, format="json")
        row = self.api.get("/api/trips/", {"state": "open"}).data[0]
        assert (row["stops_total"], row["stops_done"], row["instruments_entered"]) == (1, 1, 1)
        assert self.api.get("/api/trips/", {"state": "closed"}).data == []

    def test_reorder(self):
        trip = self.make_trip()
        url = f"/api/trips/{trip['id']}/stops/"
        ids = [self.api.post(url, {"address": f"ул Ленина, {n}"}, format="json").data["id"] for n in (1, 2, 3)]
        response = self.api.post(f"{url}reorder/", {"order": [ids[2], ids[0]]}, format="json")
        assert [stop["id"] for stop in response.data["stops"]] == [ids[2], ids[0], ids[1]]
        assert [stop["position"] for stop in response.data["stops"]] == [1, 2, 3]

    def test_stop_with_verifications_cannot_be_removed(self):
        trip = self.make_trip()
        stop = self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 1"}, format="json").data
        empty = self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 2"}, format="json").data
        self.add_verification(stop["work_order_id"])
        assert self.api.delete(f"/api/trip-stops/{stop['id']}/").status_code == 400
        assert self.api.delete(f"/api/trips/{trip['id']}/").status_code == 400
        assert self.api.delete(f"/api/trip-stops/{empty['id']}/").status_code == 204
        assert not WorkOrder.objects.filter(pk=empty["work_order_id"]).exists()

    def test_empty_trip_is_deleted_with_its_work_orders(self):
        trip = self.make_trip()
        self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 1"}, format="json")
        assert self.api.delete(f"/api/trips/{trip['id']}/").status_code == 204
        assert not Trip.objects.exists() and not WorkOrder.objects.exists()

    def test_closed_trip_takes_no_new_addresses(self):
        trip = self.make_trip()
        self.api.patch(f"/api/trips/{trip['id']}/", {"status": "done"}, format="json")
        response = self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 1"}, format="json")
        assert response.status_code == 400

    def test_point_from_the_phone_is_saved(self):
        trip = self.make_trip()
        stop = self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 1"}, format="json").data
        assert stop["latitude"] is None
        response = self.api.patch(f"/api/trip-stops/{stop['id']}/", {"latitude": 58.6, "longitude": 49.65}, format="json")
        assert (response.data["latitude"], response.data["longitude"]) == (58.6, 49.65)


class VisibilityTests(Base):
    def test_verifier_sees_only_own_trips(self):
        mine = self.make_trip(title="Моя", member_ids=[self.verifier.pk])
        self.make_trip(title="Не моя")
        api = self.as_user(self.verifier_user)
        assert [row["id"] for row in api.get("/api/trips/").data] == [mine["id"]]
        stranger = self.as_user(self.other_user)
        assert stranger.get("/api/trips/").data == []
        assert stranger.get(f"/api/trips/{mine['id']}/").status_code == 404
        assert stranger.post(f"/api/trips/{mine['id']}/stops/", {"address": "ул Ленина, 1"}, format="json").status_code == 404

    def test_verifier_creates_own_trip_but_not_procurement(self):
        api = self.as_user(self.verifier_user)
        response = api.post("/api/trips/", {"destination": "Белая Холуница", "lead_id": self.owner.pk}, format="json")
        assert response.status_code == 201
        assert response.data["lead"]["id"] == self.verifier.pk  # чужого старшим поверитель назначить не может
        assert response.data["title"] == "Белая Холуница"
        denied = api.post("/api/procurements/", {"title": "Поверка", "number": "1", "customer_name": "МУП"}, format="json")
        assert denied.status_code == 403

    def test_work_orders_carry_trip_and_can_be_filtered(self):
        trip = self.make_trip()
        stop = self.api.post(f"/api/trips/{trip['id']}/stops/", {"address": "ул Ленина, 1"}, format="json").data
        client = Client.objects.create(name="Городской клиент")
        site = Site.objects.create(client=client, address="Киров, ул Воровского, 1")
        city = WorkOrder.objects.create(client=client, site=site, assigned_employee=self.owner)

        rows = {row["id"]: row for row in self.api.get("/api/work-orders/").data}
        assert rows[stop["work_order_id"]]["trip_id"] == trip["id"]
        assert rows[stop["work_order_id"]]["trip_title"] == "Слободской район"
        assert rows[city.pk]["trip_id"] is None
        assert [row["id"] for row in self.api.get("/api/work-orders/", {"trip": "none"}).data] == [city.pk]
        assert [row["id"] for row in self.api.get("/api/work-orders/", {"id": str(stop["work_order_id"])}).data] == [
            stop["work_order_id"]
        ]


class ProcurementTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def make_procurement(self, **extra) -> dict:
        payload = {
            "number": "0340200003326001234", "law": "44", "title": "Поверка счётчиков воды",
            "customer_name": "МУП «Водоканал»", "customer_inn": "4345000249",
            "contract_number": "17-П", "contract_date": "2026-10-20", "deadline": "2026-12-25",
            "items": [{"title": "Счётчики воды Ду 15", "quantity": 120}, {"title": "Счётчики воды Ду 50", "quantity": 8}],
            **extra,
        }
        response = self.api.post("/api/procurements/", payload, format="json")
        assert response.status_code == 201, response.data
        return response.data

    def test_create(self):
        data = self.make_procurement()
        assert data["label"] == "№ 0340200003326001234" and data["customer"] == "МУП «Водоканал»"
        assert data["progress"] == {"planned": 128, "entered": 0, "ready": 0, "unsuitable": 0}
        assert "zakupki.gov.ru" in data["open_url"]
        assert Client.objects.get(inn="4345000249").kind == ClientKind.ORG
        assert data["responsible"]["id"] == self.owner.pk

    def test_number_or_contract_is_required(self):
        response = self.api.post("/api/procurements/", {"title": "Поверка", "customer_name": "МУП"}, format="json")
        assert response.status_code == 400
        bad_inn = self.api.post("/api/procurements/", {"title": "П", "number": "1", "customer_name": "МУП", "customer_inn": "12"},
                                format="json")
        assert bad_inn.status_code == 400

    def test_same_inn_reuses_the_customer(self):
        self.make_procurement()
        self.make_procurement(number="0340200003326009999", customer_name="МУП Водоканал г. Кирова")
        assert Client.objects.filter(inn="4345000249").count() == 1

    def test_visits_share_the_contract_and_carry_unfinished_addresses(self):
        procurement = self.make_procurement()
        first = self.api.post("/api/trips/", {"procurement_id": procurement["id"], "date_start": "2026-11-14",
                                             "date_end": "2026-11-15", "member_ids": [self.verifier.pk]}, format="json").data
        assert first["kind"] == "procurement" and first["title"] == "МУП «Водоканал» — заход 1"
        assert first["visit_number"] == 1

        url = f"/api/trips/{first['id']}/stops/"
        done = self.api.post(url, {"address": "Насосная станция № 1", "is_restricted": True,
                                   "access_note": "Проходная, паспорт"}, format="json").data
        left = self.api.post(url, {"address": "Котельная № 4", "note": "ключ у сторожа"}, format="json").data
        refused = self.api.post(url, {"address": "Склад"}, format="json").data
        # Клиент адреса по закупке — всегда заказчик, как бы ни назвали встречающего.
        assert done["client"] == "МУП «Водоканал»" and done["is_restricted"] is True

        self.add_verification(done["work_order_id"], "W1")
        self.add_verification(done["work_order_id"], "W2", suitable=False)
        self.api.patch(f"/api/trip-stops/{done['id']}/", {"status": "done"}, format="json")
        self.api.patch(f"/api/trip-stops/{left['id']}/", {"status": "postponed"}, format="json")
        self.api.patch(f"/api/trip-stops/{refused['id']}/", {"status": "refused"}, format="json")

        response = self.api.post(f"/api/trips/{first['id']}/next-visit/", {"date_start": "2026-12-01"}, format="json")
        assert response.status_code == 201, response.data
        second = response.data
        assert second["title"] == "МУП «Водоканал» — заход 2" and second["visit_number"] == 2
        assert [m["id"] for m in second["members"]] == [self.verifier.pk]
        assert [stop["address"] for stop in second["stops"]] == ["Котельная № 4"]
        carried = second["stops"][0]
        assert carried["note"] == "ключ у сторожа" and carried["status"] == "planned"
        assert carried["carried_from"] == "МУП «Водоканал» — заход 1"
        assert carried["work_order_id"] != left["work_order_id"]  # у каждого захода свои наряды
        assert WorkOrder.objects.get(pk=carried["work_order_id"]).scheduled_date == dt.date(2026, 12, 1)

        self.add_verification(carried["work_order_id"], "W3")
        card = self.api.get(f"/api/procurements/{procurement['id']}/").data
        assert card["progress"] == {"planned": 128, "entered": 3, "ready": 3, "unsuitable": 1}
        assert [trip["id"] for trip in card["trips"]] == [first["id"], second["id"]]
        assert {site["address"] for site in card["sites"]} == {"Насосная станция № 1", "Котельная № 4", "Склад"}

    def test_passes_show_who_is_covered_for_the_trip_dates(self):
        procurement = self.make_procurement()
        trip = self.api.post("/api/trips/", {"procurement_id": procurement["id"], "date_start": "2026-11-14",
                                            "date_end": "2026-11-15", "member_ids": [self.verifier.pk]}, format="json").data
        assert trip["passes"] == []  # пропусков по закупке не заводили — значит, не нужны

        base = f"/api/procurements/{procurement['id']}/passes/"
        ok = self.api.post(base, {"employee_id": self.owner.pk, "status": "issued", "valid_from": "2026-11-01",
                                  "valid_to": "2026-12-25", "number": "А-15"}, format="json")
        assert ok.status_code == 201, ok.data
        short = self.api.post(base, {"employee_id": self.verifier.pk, "status": "issued", "valid_to": "2026-11-14"},
                              format="json").data
        car = self.api.post(base, {"vehicle": "Лада Ларгус А123ВС43", "status": "requested"}, format="json")
        assert car.status_code == 201 and car.data["holder"] == "Лада Ларгус А123ВС43"
        assert self.api.post(base, {"status": "issued"}, format="json").status_code == 400

        states = {row["employee_id"]: row for row in self.api.get(f"/api/trips/{trip['id']}/").data["passes"]}
        assert states[self.owner.pk]["state"] == "ok" and "25.12.2026" in states[self.owner.pk]["text"]
        assert states[self.verifier.pk]["state"] == "missing"  # истекает в первый же день

        self.api.patch(f"/api/passes/{short['id']}/", {"valid_to": "2026-11-30"}, format="json")
        states = {row["employee_id"]: row for row in self.api.get(f"/api/trips/{trip['id']}/").data["passes"]}
        assert states[self.verifier.pk]["state"] == "ok"
        assert self.api.delete(f"/api/passes/{short['id']}/").status_code == 204
        assert Pass.objects.filter(status=PassStatus.ISSUED).count() == 1

    def test_documents_are_served_only_to_those_who_see_the_procurement(self):
        with override_settings(MEDIA_ROOT=self.media, SENDFILE_ROOT=self.media):
            procurement = self.make_procurement()
            upload = SimpleUploadedFile("Контракт 17-П.pdf", b"%PDF-1.4 contract", content_type="application/pdf")
            created = self.api.post(f"/api/procurements/{procurement['id']}/documents/",
                                    {"file": upload, "kind": "contract"}, format="multipart")
            assert created.status_code == 201, created.data
            assert created.data["title"] == "Контракт 17-П" and created.data["kind_display"] == "Контракт"
            url = f"/api/procurement-documents/{created.data['id']}/"

            got = self.api.get(url)
            assert got.status_code == 200 and _body(got) == b"%PDF-1.4 contract"

            verifier = self.as_user(self.verifier_user)
            assert verifier.get(url).status_code == 404  # не едет по этой закупке
            assert verifier.get(f"/api/procurements/{procurement['id']}/").status_code == 404
            self.api.post("/api/trips/", {"procurement_id": procurement["id"], "member_ids": [self.verifier.pk]}, format="json")
            assert verifier.get(url).status_code == 200
            assert verifier.get(f"/api/procurements/{procurement['id']}/").data["can_edit"] is False
            assert verifier.delete(url).status_code == 403

            assert self.api.delete(url).status_code == 204
            assert self.api.get(f"/api/procurements/{procurement['id']}/").data["documents"] == []

    def test_patch_replaces_scope(self):
        procurement = self.make_procurement()
        response = self.api.patch(f"/api/procurements/{procurement['id']}/",
                                  {"status": "done", "items": [{"title": "Счётчики", "quantity": 5}]}, format="json")
        assert response.status_code == 200, response.data
        assert response.data["status"] == "done" and response.data["progress"]["planned"] == 5
        assert Procurement.objects.get().customer.name == "МУП «Водоканал»"


class GeocodeTests(TestCase):
    def test_off_never_calls_out(self):
        with mock.patch("apps.logistics.geocode.requests.get") as get:
            assert geocode.geocode("Киров, ул Ленина, 1") is None
            get.assert_not_called()

    def test_address_is_split_into_place_and_the_rest(self):
        split = geocode.split_address
        assert split("г Слободской, ул Советская, д 10, кв 5") == ("Слободской", "ул Советская, 10")
        assert split("Кировская обл, Слободской р-н, д. Стулово, ул Мира, 14") == ("Стулово", "ул Мира, 14")
        assert split("ул Ленина, 3", hint="г. Слободской") == ("Слободской", "ул Ленина, 3")
        assert split("ул Ленина, 3", hint="Слободской район") == ("", "ул Ленина, 3")  # район — не населённый пункт
        assert split("ул Ленина, 3") == ("", "ул Ленина, 3")

    @override_settings(GEOCODER="nominatim")
    def test_nominatim_answer_must_be_in_the_same_settlement(self):
        # Живой ответ: по «Слободской, ул Советская, 10» первой идёт деревня Бобино Слободского района.
        found = [
            {"lat": "58.6971", "lon": "49.7610",
             "display_name": "10, Советская улица, Бобино, Бобинское сельское поселение, Слободской район, Кировская область"},
            {"lat": "58.7312", "lon": "50.1668",
             "display_name": "10, Советская улица, Слободской, городской округ Слободской, Кировская область"},
        ]
        answer = mock.Mock(**{"json.return_value": found, "raise_for_status.return_value": None})
        with mock.patch("apps.logistics.geocode.requests.get", return_value=answer) as get:
            assert geocode.geocode("Слободской, ул Советская, 10, кв 5") == (58.7312, 50.1668)
            assert get.call_args.kwargs["params"]["q"] == "Кировская область, Слободской, ул Советская, 10"
            assert "User-Agent" in get.call_args.kwargs["headers"]
        only_wrong = mock.Mock(**{"json.return_value": found[:1], "raise_for_status.return_value": None})
        with mock.patch("apps.logistics.geocode.requests.get", return_value=only_wrong):
            assert geocode.geocode("Слободской, ул Советская, 10") is None  # чужая деревня хуже, чем ничего

    @override_settings(GEOCODER="nominatim")
    def test_address_without_settlement_is_not_guessed(self):
        with mock.patch("apps.logistics.geocode.requests.get") as get:
            assert geocode.geocode("ул Ленина, 3") is None
            get.assert_not_called()

    @override_settings(GEOCODER="nominatim")
    def test_failure_is_just_no_point(self):
        import requests

        with mock.patch("apps.logistics.geocode.requests.get", side_effect=requests.ConnectionError("нет сети")):
            assert geocode.geocode("Слободской, ул Советская, 10") is None

    @override_settings(GEOCODER="nominatim")
    def test_locate_missing_fills_sites(self):
        _, lead = make_user("lead@ei.test", Role.OWNER, "09", "Старший С. С.")
        trip = Trip.objects.create(title="Тест", lead=lead)
        with mock.patch("apps.logistics.geocode.geocode", return_value=None):
            stop = service.add_stop(trip, service.StopInput(address="Слободской, ул Советская, 10"))
        assert stop.site.latitude is None
        with mock.patch("apps.logistics.geocode.geocode", return_value=(58.73, 50.17)) as found:
            result = service.locate_missing(trip)
        assert found.call_args.args == ("Слободской, ул Советская, 10", "")
        assert result == {"found": 1, "not_found": 0, "remaining": 0}
        assert float(TripStop.objects.get().site.latitude) == 58.73
        assert StopStatus.PLANNED == TripStop.objects.get().status
