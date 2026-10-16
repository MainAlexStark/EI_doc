"""Заведение типов СИ без админки: поверитель заводит, метролог проверяет."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Role, User

LIMITS = {
    "q_min": 0.03, "q_transition_a": 0.15, "q_transition_b": 0.12, "q_nominal": 1.5,
    "q_max": 3, "error_below_transition": 5, "error_above_transition": 2,
}


class SiTypeApiTestCase(TestCase):
    def setUp(self) -> None:
        self.family = MeasurementFamily.objects.create(
            code="water-meter", name="Счётчики воды", calculator_key="water_meter"
        )
        SiType.objects.create(
            family=self.family, registry_number="12345-06", name="СВК-15", limits=LIMITS,
        )
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.metrologist = User.objects.create_user("m@ei.test", "pw", role=Role.METROLOGIST)
        self.observer = User.objects.create_user("o@ei.test", "pw", role=Role.OBSERVER)

    def as_user(self, user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def create(self, user, **overrides):
        body = {"registry_number": "55555-20", "name": "ТЕСТ-15", "manufacturer": "Завод", "limits": LIMITS}
        body.update(overrides)
        return self.as_user(user).post(reverse("si_type_list"), body, format="json")

    def test_verifier_creates_type_waiting_for_review(self):
        response = self.create(self.verifier, limits={**LIMITS, "q_max": "3,0"})
        assert response.status_code == 201, response.data
        si_type = SiType.objects.get(registry_number="55555-20")
        assert si_type.needs_review
        assert si_type.created_by == self.verifier
        assert si_type.family == self.family
        assert si_type.limits["q_max"] == 3.0

    def test_metrologist_type_is_reviewed_at_once(self):
        assert self.create(self.metrologist).status_code == 201
        si_type = SiType.objects.get(registry_number="55555-20")
        assert not si_type.needs_review
        assert si_type.reviewed_by == self.metrologist

    def test_observer_cannot_create(self):
        assert self.create(self.observer).status_code == 403

    def test_duplicate_returns_existing(self):
        response = self.create(self.verifier, registry_number="12345-06", name="СВК-15")
        assert response.status_code == 200
        assert response.data["existing"] is True
        assert SiType.objects.count() == 1

    def test_limits_validated_with_human_message(self):
        response = self.create(self.verifier, limits={**LIMITS, "q_min": ""})
        assert response.status_code == 400
        assert "Q наименьший" in str(response.data)
        response = self.create(self.verifier, limits={**LIMITS, "q_min": 5})
        assert response.status_code == 400
        assert "меньше" in str(response.data)

    def test_confirm_only_by_metrologist(self):
        self.create(self.verifier)
        si_type = SiType.objects.get(registry_number="55555-20")
        url = reverse("si_type_confirm", args=[si_type.pk])
        assert self.as_user(self.verifier).post(url).status_code == 403
        assert self.as_user(self.metrologist).post(url).status_code == 200
        si_type.refresh_from_db()
        assert not si_type.needs_review and si_type.reviewed_by == self.metrologist

    def test_author_may_fix_own_unreviewed_type_only(self):
        self.create(self.verifier)
        si_type = SiType.objects.get(registry_number="55555-20")
        url = reverse("si_type_detail", args=[si_type.pk])
        response = self.as_user(self.verifier).patch(url, {"manufacturer": "Другой завод"}, format="json")
        assert response.status_code == 200, response.data
        self.as_user(self.metrologist).post(reverse("si_type_confirm", args=[si_type.pk]))
        response = self.as_user(self.verifier).patch(url, {"manufacturer": "Ещё"}, format="json")
        assert response.status_code == 403

    def test_list_filters_needs_review(self):
        self.create(self.verifier)
        data = self.as_user(self.metrologist).get(reverse("si_type_list"), {"needs_review": "1"}).data
        assert [item["registry_number"] for item in data] == ["55555-20"]

    def test_presets_come_from_reviewed_types(self):
        SiType.objects.create(
            family=self.family, registry_number="1-1", name="X-20",
            limits={**LIMITS, "q_min": 0.05, "q_transition_a": 0.25, "q_transition_b": 0.2,
                    "q_nominal": 2.5, "q_max": 5},
        )
        SiType.objects.create(family=self.family, registry_number="2-2", name="Y-15", limits=LIMITS)
        data = self.as_user(self.verifier).get(reverse("si_type_presets")).data
        presets = data["presets"]
        assert presets[0]["label"] == "Ду15" and presets[0]["count"] == 2
        assert presets[1]["label"] == "Ду20"
        assert data["can_review"] is False
        assert [f["key"] for f in data["fields"]][0] == "q_min"
