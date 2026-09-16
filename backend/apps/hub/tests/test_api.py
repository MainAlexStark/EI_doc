"""Приём заявки с сайта и диспетчерская очередь."""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import District
from apps.core.models import Employee, User
from apps.hub.address import AddressSuggestion, AddressSuggestUnavailable
from apps.hub.models import DistrictAssignment, Request, RequestStatus
from apps.verification.models import WorkOrder


class RequestCreateTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        # AnonRateThrottle (scope "anon", 20/час) считает по LocMemCache, который
        # тесты не откатывают вместе с БД — без очистки бюджет утекает между
        # файлами по порядку запуска, и не связанный с этим тест мог бы поймать 429.
        cache.clear()

    def payload(self, **overrides):
        data = {
            "contact_name": "Иванов И. И.",
            "contact_phone": "+7 (999) 000-00-00",
            "address": "г. Киров, ул. Ленина, 1",
            "district": "Ленинский район",
            "si_description": "Счётчик воды",
            "consent_given": True,
        }
        data.update(overrides)
        return data

    def test_anonymous_can_submit_a_request(self):
        response = self.api.post(reverse("request_create"), self.payload(), format="json")
        assert response.status_code == 201
        obj = Request.objects.get(pk=response.data["id"])
        assert obj.status == RequestStatus.ROUTED  # роутинг срабатывает сразу
        assert obj.district.name == "Ленинский район"
        assert obj.consent_given is True

    def test_missing_consent_is_rejected(self):
        response = self.api.post(
            reverse("request_create"), self.payload(consent_given=False), format="json"
        )
        assert response.status_code == 400
        assert "consent_given" in response.data

    def test_absent_consent_field_is_rejected(self):
        payload = self.payload()
        del payload["consent_given"]
        response = self.api.post(reverse("request_create"), payload, format="json")
        assert response.status_code == 400

    def test_missing_contact_is_rejected(self):
        response = self.api.post(
            reverse("request_create"),
            self.payload(contact_phone="", contact_email=""),
            format="json",
        )
        assert response.status_code == 400

    def test_badly_formatted_phone_is_rejected(self):
        response = self.api.post(
            reverse("request_create"),
            self.payload(contact_phone="8 999 000 00 00"),
            format="json",
        )
        assert response.status_code == 400
        assert "contact_phone" in response.data

    def test_honeypot_field_marks_the_request_as_spam_without_routing(self):
        response = self.api.post(reverse("request_create"), self.payload(website="http://spam"), format="json")
        assert response.status_code == 201
        obj = Request.objects.get(pk=response.data["id"])
        assert obj.status == RequestStatus.SPAM
        assert obj.honeypot_tripped is True
        assert obj.district is None  # спам не засоряет справочник районов


class AddressSuggestTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()

    @mock.patch("apps.hub.api.DaDataClient")
    def test_not_configured_returns_empty_without_calling_dadata(self, client_cls):
        client_cls.return_value.is_configured = False
        response = self.api.get(reverse("address_suggest"), {"q": "Лен"})
        assert response.data == {"configured": False, "results": []}
        client_cls.return_value.suggest.assert_not_called()

    @mock.patch("apps.hub.api.DaDataClient")
    def test_returns_parsed_suggestions(self, client_cls):
        client_cls.return_value.is_configured = True
        client_cls.return_value.suggest.return_value = [
            AddressSuggestion(value="г Киров, ул Ленина, 1", postal_code="610000", district="Ленинский р-н")
        ]
        response = self.api.get(reverse("address_suggest"), {"q": "Лен"})
        assert response.data["configured"] is True
        assert response.data["results"][0]["postal_code"] == "610000"

    @mock.patch("apps.hub.api.DaDataClient")
    def test_upstream_failure_does_not_break_the_form(self, client_cls):
        client_cls.return_value.is_configured = True
        client_cls.return_value.suggest.side_effect = AddressSuggestUnavailable("нет сети")
        response = self.api.get(reverse("address_suggest"), {"q": "Лен"})
        assert response.status_code == 200
        assert response.data["results"] == []


class DispatchTestCase(TestCase):
    def setUp(self) -> None:
        self.employee = Employee.objects.create(tab_number="05", full_name="Стариков А. А.")
        self.user = User.objects.create_user("dispatch@ei.test", "pw")
        self.district = District.objects.create(name="Ленинский район")
        DistrictAssignment.objects.create(district=self.district, employee=self.employee, priority=10)

        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.request_obj = Request.objects.create(
            contact_name="Петров П. П.", contact_phone="+7 (999) 111-22-33",
            address="г. Киров, ул. Ленина, 1", district=self.district,
        )
        from apps.hub.routing import route
        route(self.request_obj)

    def test_list_shows_the_suggestion(self):
        response = self.api.get(reverse("request_list"))
        row = response.data[0]
        assert row["suggested_employee_id"] == self.employee.pk

    def test_confirm_creates_a_work_order_and_marks_the_request_confirmed(self):
        response = self.api.post(
            reverse("request_confirm", args=[self.request_obj.pk]),
            {"employee_id": self.employee.pk, "scheduled_date": "2026-10-01"},
            format="json",
        )
        assert response.status_code == 201
        work_order = WorkOrder.objects.get(pk=response.data["work_order_id"])
        assert work_order.assigned_employee_id == self.employee.pk
        assert work_order.site.address == self.request_obj.address

        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.CONFIRMED
        assert self.request_obj.assigned_employee_id == self.employee.pk

    def test_confirming_twice_is_refused(self):
        self.api.post(
            reverse("request_confirm", args=[self.request_obj.pk]),
            {"employee_id": self.employee.pk}, format="json",
        )
        response = self.api.post(
            reverse("request_confirm", args=[self.request_obj.pk]),
            {"employee_id": self.employee.pk}, format="json",
        )
        assert response.status_code == 400

    def test_reject_marks_spam_when_flagged(self):
        response = self.api.post(
            reverse("request_reject", args=[self.request_obj.pk]),
            {"reason": "дубль", "spam": True}, format="json",
        )
        assert response.data["status"] == RequestStatus.SPAM
