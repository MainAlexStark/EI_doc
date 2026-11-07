"""Отправка результатов поверки в Аршин: готовность, пакет, XML, итог, сверка."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.arshin import submission as service
from apps.arshin.models import ArshinSubmissionItem, ItemStatus, SubmissionStatus
from apps.catalog.models import Standard, VerificationMethod
from apps.core.models import Role, User
from apps.verification import numbering
from apps.verification.models import ProtocolStatus
from apps.verification.tests import factories as f

NS = service.DEFAULT_NAMESPACE


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


class FakeFif:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search_vri(self, params):
        self.calls.append(params)
        return self.rows


@override_settings(ARSHIN_SIGN_CIPHER="ГЕН", MEDIA_ROOT="/tmp/ei-doc-test-media", SENDFILE_ROOT="/tmp/ei-doc-test-media")
class SubmissionTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.method = VerificationMethod.objects.create(designation="МИ 1592-2015")
        self.standard = Standard.objects.create(
            fif_number="3.2.ААА.0001.2024", name="Установка поверочная",
            verified_at=dt.date(2026, 1, 1), valid_until=dt.date(2027, 1, 1),
        )

    def signed(self, day: int, *, suitable: bool = True, reason: str = ""):
        protocol = f.draft(self.family, self.employee, day=day)
        v = protocol.verification
        v.method = self.method
        v.suitable = suitable
        v.unsuitability_reason = reason
        v.next_verification_date = dt.date(2032, 3, day) if suitable else None
        v.temperature = 21.5
        v.save()
        v.standards.add(self.standard)
        numbering.assign_numbers(protocol.scope)
        protocol.refresh_from_db()
        numbering.seal(protocol, signed_by=self.employee)
        protocol.refresh_from_db()
        return protocol

    def test_unsigned_and_incomplete_protocols_are_explained(self):
        draft = f.draft(self.family, self.employee, day=2)
        issues = service.problems(draft)
        assert any("не подписан" in i for i in issues)
        assert any("методика" in i for i in issues)
        assert any("эталоны" in i for i in issues)

    @override_settings(ARSHIN_SIGN_CIPHER="")
    def test_missing_cipher_blocks_the_batch(self):
        protocol = self.signed(3)
        result = service.create_submission([protocol.pk])
        assert result.submission is None
        assert "ARSHIN_SIGN_CIPHER" in result.refused[0]["problems"][0]

    def test_xml_has_one_result_per_protocol(self):
        good = self.signed(3)
        bad = self.signed(4, suitable=False, reason="Погрешность вне допуска")
        result = service.create_submission()
        sub = result.submission
        assert sub.items.count() == 2 and len(sub.sha256) == 64

        root = ET.fromstring(sub.xml.read())
        results = root.findall(_q("result"))
        assert len(results) == 2
        first = results[0]
        assert first.find(f"{_q('miInfo')}/{_q('singleMI')}/{_q('manufactureNum')}").text == "SN003"
        assert first.find(_q("signCipher")).text == "ГЕН"
        assert first.find(_q("vrfDate")).text == "2026-03-03"
        assert first.find(_q("validDate")).text == "2032-03-03"
        assert first.find(_q("applicable")) is not None
        assert first.find(f"{_q('means')}/{_q('mieta')}/{_q('number')}").text == "3.2.ААА.0001.2024"
        assert good.full_number in first.find(_q("additional_info")).text
        second = results[1]
        assert second.find(f"{_q('inapplicable')}/{_q('reasons')}").text == "Погрешность вне допуска"
        assert second.find(_q("validDate")) is None
        assert bad.pk in [i.protocol_id for i in sub.items.all()]

    def test_protocol_is_never_in_two_active_batches(self):
        protocol = self.signed(3)
        first = service.create_submission([protocol.pk]).submission
        again = service.create_submission([protocol.pk])
        assert again.submission is None
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArshinSubmissionItem.objects.create(submission=first, protocol=protocol)

    def test_rejected_protocol_goes_back_to_the_queue(self):
        protocol = self.signed(3)
        sub = service.create_submission([protocol.pk]).submission
        service.mark_uploaded(sub)
        service.reject(sub.items.get(), "Неверный номер типа")
        sub.refresh_from_db()
        assert sub.status == SubmissionStatus.DONE
        assert protocol in service.eligible_protocols()
        assert service.create_submission([protocol.pk]).submission is not None

    def test_accept_publishes_the_protocol(self):
        protocol = self.signed(3)
        sub = service.create_submission([protocol.pk]).submission
        service.mark_uploaded(sub)
        service.accept(sub.items.get(), "1-123456789")
        protocol.refresh_from_db()
        sub.refresh_from_db()
        assert protocol.status == ProtocolStatus.PUBLISHED
        assert protocol.fif_record_number == "1-123456789"
        assert sub.status == SubmissionStatus.DONE

    def test_reconcile_picks_up_record_numbers(self):
        protocol = self.signed(3)
        other = self.signed(4)
        sub = service.create_submission().submission
        service.mark_uploaded(sub)
        fif = FakeFif([{"vri_id": "1-555", "mi_number": "SN003", "verification_date": "03.03.2026"}])
        outcome = service.reconcile(sub, client=fif)
        assert outcome == {"accepted": 1, "waiting": 1}
        protocol.refresh_from_db()
        other.refresh_from_db()
        assert protocol.fif_record_number == "1-555"
        assert other.status == ProtocolStatus.SIGNED
        assert fif.calls[0]["mi_number"] == "SN003"

    def test_cancel_releases_protocols(self):
        protocol = self.signed(3)
        sub = service.create_submission([protocol.pk]).submission
        service.cancel(sub)
        assert sub.items.get().status == ItemStatus.REJECTED
        assert protocol in service.eligible_protocols()


@override_settings(ARSHIN_SIGN_CIPHER="ГЕН", MEDIA_ROOT="/tmp/ei-doc-test-media", SENDFILE_ROOT="/tmp/ei-doc-test-media")
class ApiTestCase(SubmissionTestCase):
    def test_api_flow_and_permissions(self):
        protocol = self.signed(3)
        api = APIClient()
        api.force_authenticate(User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER))
        assert api.post(reverse("arshin_submissions"), {}, format="json").status_code == 403

        api.force_authenticate(User.objects.create_user("m@ei.test", "pw", role=Role.METROLOGIST))
        queue = api.get(reverse("arshin_queue")).json()
        assert queue["protocols"][0]["problems"] == []

        created = api.post(reverse("arshin_submissions"), {"protocols": [protocol.pk]}, format="json")
        assert created.status_code == 201, created.content
        sub_id = created.json()["submission"]["id"]
        xml = api.get(reverse("arshin_submission_xml", args=[sub_id]))
        assert xml.status_code == 200

        uploaded = api.post(reverse("arshin_submission_action", args=[sub_id, "uploaded"]))
        assert uploaded.json()["status"] == SubmissionStatus.UPLOADED
        item_id = uploaded.json()["items"][0]["id"]
        result = api.post(reverse("arshin_item_result", args=[item_id]),
                          {"accepted": True, "fif_record_number": "1-42"}, format="json")
        assert result.json()["status"] == ItemStatus.ACCEPTED
