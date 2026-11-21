"""Готовность к нагрузке: число запросов к базе на главных списках, страницы, «Сегодня», кэш, IP за прокси."""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.core.cache import cache as django_cache
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core import cache, net
from apps.core.models import Employee, Role, User
from apps.hub.models import Request, RequestItem
from apps.logistics.models import Procurement, ProcurementItem, Trip, TripKind
from apps.logistics.service import attach_to_city_day
from apps.verification.models import (
    Client,
    ClientKind,
    Instrument,
    Site,
    Verification,
    VerificationStatus,
    WorkOrder,
    WorkOrderKind,
)


class LoadBase(TestCase):
    def setUp(self) -> None:
        django_cache.clear()
        self.user = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        self.me = Employee.objects.create(tab_number="01", full_name="Менеджер М. М.", user=self.user)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        family = MeasurementFamily.objects.create(code="water", name="Счётчики воды")
        self.family = family
        self.si = SiType.objects.create(registry_number="1-20", name="СВ-15", family=family)
        self.customer = Client.objects.create(kind=ClientKind.ORG, name="МУП «Водоканал»", inn="4345000249")
        self.n = 0

    def order(self, *, kind=WorkOrderKind.FIELD, verifications: int = 2) -> WorkOrder:
        self.n += 1
        site = Site.objects.create(client=self.customer, address=f"ул Мира, {self.n}")
        order = WorkOrder.objects.create(client=self.customer, site=site, assigned_employee=self.me, kind=kind,
                                         scheduled_date=dt.date.today())
        for i in range(verifications):
            instrument = Instrument.objects.create(si_type=self.si, serial_number=f"{self.n}-{i}")
            Verification.objects.create(work_order=order, instrument=instrument, verifier=self.me,
                                        verified_at="2026-11-20T10:00:00+03:00",
                                        status=VerificationStatus.READY if i else VerificationStatus.DRAFT)
        return order

    def queries(self, url: str, params: dict | None = None) -> tuple[int, object]:
        with CaptureQueriesContext(connection) as ctx:
            response = self.api.get(url, params or {})
        assert response.status_code == 200, response.data
        return len(ctx.captured_queries), response


class ListQueryCountTests(LoadBase):
    def test_work_orders_list_does_not_grow_with_rows(self):
        for _ in range(3):
            attach_to_city_day(self.order())
        few, _ = self.queries("/api/work-orders/")
        for _ in range(7):
            self.order(kind=WorkOrderKind.LAB)
        many, response = self.queries("/api/work-orders/")
        assert len(response.data) == 10 and many == few and many <= 4
        lab = [row for row in response.data if row["kind"] == "lab"][0]
        assert lab["verifications_total"] == 2 and lab["verifications_ready"] == 1

    def test_requests_list(self):
        for n in range(3):
            r = Request.objects.create(contact_name=f"К{n}", address="ул Мира", consent_given=True)
            RequestItem.objects.create(request=r, family=self.family, quantity=2, unit_price=500)
        few, _ = self.queries("/api/hub/requests/list/")
        for n in range(12):
            Request.objects.create(contact_name=f"Е{n}", address="ул Мира", consent_given=True)
        many, response = self.queries("/api/hub/requests/list/")
        assert len(response.data) == 15 and many == few and many <= 3

    def test_procurements_list(self):
        for n in range(2):
            p = Procurement.objects.create(number=str(n), title="Поверка", customer=self.customer)
            ProcurementItem.objects.create(procurement=p, title="Счётчики", quantity=10)
        few, _ = self.queries("/api/procurements/")
        for n in range(8):
            p = Procurement.objects.create(number=f"x{n}", title="Поверка", customer=self.customer)
            order = self.order(kind=WorkOrderKind.LAB)
            order.procurement = p
            order.save()
        many, response = self.queries("/api/procurements/")
        assert len(response.data) == 10 and many == few and many <= 5
        assert {row["progress"]["entered"] for row in response.data} == {0, 2}

    def test_trips_list(self):
        for n in range(3):
            Trip.objects.create(title=f"Т{n}", kind=TripKind.CLASSIC, lead=self.me, date_start=dt.date.today())
        few, _ = self.queries("/api/trips/")
        for n in range(9):
            Trip.objects.create(title=f"Е{n}", kind=TripKind.CLASSIC, lead=self.me, date_start=dt.date.today())
        many, _ = self.queries("/api/trips/")
        assert many == few and many <= 4


class PagingTests(LoadBase):
    def test_array_by_default_pages_on_request(self):
        for _ in range(5):
            self.order(verifications=0)
        assert len(self.api.get("/api/work-orders/", {"limit": 2}).data) == 2
        page = self.api.get("/api/work-orders/", {"page_size": 2, "page": 3}).data
        assert page["count"] == 5 and len(page["results"]) == 1 and page["next_page"] is None
        page = self.api.get("/api/hub/requests/list/", {"page_size": 10}).data
        assert page == {"count": 0, "page": 1, "page_size": 10, "next_page": None, "results": []}


class TodayTests(LoadBase):
    def test_summary_is_fast_and_counts_mine(self):
        Request.objects.create(contact_name="Новая", address="ул Мира", consent_given=True, suggested_employee=self.me)
        attach_to_city_day(self.order(verifications=0))
        ready = self.order(kind=WorkOrderKind.LAB, verifications=1)
        Verification.objects.filter(work_order=ready).update(status=VerificationStatus.READY)
        self.order(kind=WorkOrderKind.LAB, verifications=2)
        Trip.objects.create(title="Слободской", kind=TripKind.CLASSIC, lead=self.me,
                            date_start=dt.date.today(), date_end=dt.date.today() + dt.timedelta(days=2))
        Procurement.objects.create(number="1", title="Поверка", customer=self.customer)
        count, response = self.queries("/api/core/today/")
        data = response.data
        assert count <= 12, count
        assert data["scope"] == "company" and data["requests"] == {"new": 1, "mine": 1}
        assert data["stops"]["total"] == 1 and data["stops"]["waiting"] == 1
        assert data["intake"] == {"in_work": 1, "ready_to_issue": 1}
        assert data["trips"]["count"] == 1 and data["procurements"]["active"] == 1
        assert data["protocols"]["drafts"] == 1 and data["protocols"]["on_review"] == 2
        assert set(data["review"]) == {"si_types", "verification_templates", "attestation_templates"}

    def test_verifier_sees_only_own(self):
        user = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)
        api = APIClient()
        api.force_authenticate(user)
        self.order(kind=WorkOrderKind.LAB)  # чужая приёмка
        data = api.get("/api/core/today/").data
        assert data["scope"] == "mine" and data["intake"] == {"in_work": 0, "ready_to_issue": 0}
        assert data["protocols"] == {"drafts": 0, "rejected": 0, "on_review": 0}


class InfraTests(TestCase):
    def test_remember_keeps_only_successful_values(self):
        calls = []

        def compute():
            calls.append(1)
            return None if len(calls) == 1 else {"ok": True}

        with override_settings(EXTERNAL_CACHE_ENABLED=True):
            django_cache.clear()
            key = cache.key("test", "x")
            assert cache.remember(key, 60, compute) is None  # неудача не кэшируется
            assert cache.remember(key, 60, compute) == {"ok": True}
            assert cache.remember(key, 60, compute) == {"ok": True}
        assert len(calls) == 2

    def test_remember_survives_broken_cache(self):
        with override_settings(EXTERNAL_CACHE_ENABLED=True), \
                mock.patch("apps.core.cache.cache.get", side_effect=ConnectionError("redis down")):
            assert cache.remember("k", 60, lambda: 5) == 5

    def test_client_ip_behind_one_proxy(self):
        # Caddy дописывает адрес посетителя последним; подделанное начало заголовка не помогает.
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.7", REMOTE_ADDR="172.18.0.5")
        with mock.patch.object(api_settings, "NUM_PROXIES", 1):
            assert net.client_ip(request) == "203.0.113.7"
        with mock.patch.object(api_settings, "NUM_PROXIES", 0):
            assert net.client_ip(request) == "172.18.0.5"

    def test_public_form_has_its_own_throttle(self):
        from apps.hub.api import PublicRequestBurstThrottle, PublicRequestThrottle, RequestCreateView

        assert RequestCreateView.throttle_classes == [PublicRequestBurstThrottle, PublicRequestThrottle]

    def test_healthz_deep_checks_cache(self):
        response = self.client.get("/healthz", {"deep": "1"})
        assert response.status_code == 200 and response.json()["cache"] == "ok"
