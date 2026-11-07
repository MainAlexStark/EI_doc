"""Договоры: сумма прописью, заполнение шаблона, номера, права."""

from __future__ import annotations

import io
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document
from rest_framework.test import APIClient

from apps.core.models import Role, User
from apps.hub import contracts
from apps.hub.models import Contract, Request, RequestItem
from apps.verification.tests import factories as f

MEDIA = "/tmp/ei-doc-test-media"


def text_of(payload: bytes) -> str:
    doc = Document(io.BytesIO(payload))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


class WordsTestCase(TestCase):
    def test_rubles_in_words(self):
        assert contracts.rubles_in_words(Decimal("1")) == "Один рубль 00 копеек"
        assert contracts.rubles_in_words(Decimal("2500")) == "Две тысячи пятьсот рублей 00 копеек"
        assert contracts.rubles_in_words(Decimal("1121.50")) == "Одна тысяча сто двадцать один рубль 50 копеек"
        assert contracts.rubles_in_words(Decimal("0")) == "Ноль рублей 00 копеек"
        assert contracts.rubles_in_words(Decimal("2000014")).startswith("Два миллиона четырнадцать рублей")


@override_settings(MEDIA_ROOT=MEDIA, SENDFILE_ROOT=MEDIA,
                   ORG_REQUISITES={"name": "ООО «Тест»", "full_name": "ООО «Тест»", "inn": "4345000000",
                                   "director": "Иванов И. И.", "address": "Киров"})
class ContractTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.family.price = Decimal("750")
        self.request_obj = Request.objects.create(
            contact_name="Петров Пётр Петрович", contact_phone="+79991112233",
            address="г. Киров, ул. Ленина, 1",
        )
        RequestItem.objects.create(request=self.request_obj, family=self.family, quantity=2,
                                   unit_price=Decimal("750"))

    def test_contract_is_filled_from_the_request(self):
        contract = contracts.issue(request=self.request_obj)
        text = text_of(contract.file.read())
        assert contract.number.endswith("-0001")
        assert contract.number in text
        assert "Петров Пётр Петрович" in text and "Петров П.П." in text
        assert "Счётчики воды" in text
        assert "1 500,00" in text
        assert "Одна тысяча пятьсот рублей" in text
        assert "ИНН 4345000000" in text
        assert "{{" not in text and "{%" not in text
        assert contract.total == Decimal("1500") and len(contract.sha256) == 64

    def test_numbers_go_in_sequence_and_client_is_reused(self):
        first = contracts.issue(request=self.request_obj)
        second = contracts.issue(request=self.request_obj)
        assert second.seq == first.seq + 1
        assert first.client_id == second.client_id

    def test_api_and_permissions(self):
        api = APIClient()
        api.force_authenticate(User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER))
        assert api.post(reverse("contract_list"), {"request_id": self.request_obj.pk}, format="json").status_code == 403
        api.force_authenticate(User.objects.create_user("m@ei.test", "pw", role=Role.MANAGER))
        created = api.post(reverse("contract_list"), {"request_id": self.request_obj.pk}, format="json")
        assert created.status_code == 201, created.content
        file = api.get(reverse("contract_file", args=[created.json()["id"]]))
        assert file.status_code == 200
        assert Contract.objects.count() == 1
