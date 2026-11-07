"""152-ФЗ: согласие с версией политики, сроки хранения, запрос субъекта, политика."""

from __future__ import annotations

import datetime as dt

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import personal_data
from apps.core.models import PersonalDataEvent, Role, User
from apps.hub.models import Request, RequestStatus
from apps.verification.models import Client, ClientKind, Site


def _request(status=RequestStatus.DONE, days_ago=0, phone="+7 (912) 345-67-89"):
    req = Request.objects.create(contact_name="Сидоров С. С.", contact_phone=phone, contact_email="s@mail.ru",
                                 address="Киров, Ленина 1", status=status, ip_address="10.0.0.1")
    Request.objects.filter(pk=req.pk).update(created_at=timezone.now() - dt.timedelta(days=days_ago))
    req.refresh_from_db()
    return req


@override_settings(PD_RETENTION_REJECTED_DAYS=30, PD_RETENTION_DAYS=1095)
class RetentionTestCase(TestCase):
    def test_purge_respects_retention_periods(self):
        old_spam = _request(RequestStatus.SPAM, days_ago=40)
        fresh_spam = _request(RequestStatus.SPAM, days_ago=5)
        old_done = _request(RequestStatus.DONE, days_ago=1200)
        young_done = _request(RequestStatus.DONE, days_ago=400)
        assert personal_data.purge_expired(dry_run=True) == {"rejected": 1, "finished": 1}
        personal_data.purge_expired()
        for req, gone in ((old_spam, True), (fresh_spam, False), (old_done, True), (young_done, False)):
            req.refresh_from_db()
            assert bool(req.anonymized_at) is gone
        old_done.refresh_from_db()
        assert old_done.contact_phone == "" and old_done.ip_address is None
        assert old_done.contact_name == personal_data.MASK
        assert old_done.address  # адрес объекта и суммы остаются для учёта
        assert PersonalDataEvent.objects.filter(action="expired").count() == 2

    def test_legal_entity_is_not_anonymized(self):
        org = Client.objects.create(name="ООО Ромашка", kind=ClientKind.ORG, phone="+79120000000")
        assert personal_data.anonymize_client(org, action="subject") is False


class SubjectRequestTestCase(TestCase):
    def test_lookup_and_erase_by_phone(self):
        req = _request()
        other = _request(phone="+7 900 000-00-00")
        client = Client.objects.create(name="Сидоров Сергей", phone="89123456789")
        Site.objects.create(client=client, address="Киров, Ленина 1", contact="Сидоров, кв. 5")

        api = APIClient()
        api.force_authenticate(User.objects.create_user("m@ei.test", "pw", role=Role.MANAGER))
        assert api.get(reverse("personal_data"), {"phone": "9123456789"}).status_code == 403

        api.force_authenticate(User.objects.create_user("o@ei.test", "pw", role=Role.OWNER))
        found = api.get(reverse("personal_data"), {"phone": "+7 912 345 67 89"}).json()
        assert [r["id"] for r in found["requests"]] == [req.pk]
        assert [c["id"] for c in found["clients"]] == [client.pk]
        assert PersonalDataEvent.objects.filter(action="export").count() == 2

        erased = api.post(reverse("personal_data"), {"phone": "9123456789", "reason": "письмо от 01.11"},
                          format="json").json()
        assert erased == {"requests": 1, "clients": 1}
        client.refresh_from_db()
        other.refresh_from_db()
        assert client.name == "Частное лицо" and client.sites.get().contact == ""
        assert other.anonymized_at is None


class ConsentTestCase(TestCase):
    def test_public_form_stores_policy_version_and_time(self):
        api = APIClient()
        response = api.post(reverse("request_create"), {
            "contact_name": "Иван", "contact_phone": "+7 (912) 000-00-00", "address": "Киров",
            "si_description": "Два счётчика воды", "consent_given": True,
        }, format="json")
        assert response.status_code == 201, response.content
        req = Request.objects.get(pk=response.json()["id"])
        assert req.consent_at and req.consent_policy_version

    def test_policy_page_is_public(self):
        response = self.client.get(reverse("privacy_policy"))
        assert response.status_code == 200
        assert "Политика обработки персональных данных" in response.content.decode()
