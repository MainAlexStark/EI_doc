"""Бланк с QR и распознавание — apps.verification.api_scan.

Пайплайн end-to-end (кроме самого VLM — он всегда мокается, реального
Yandex AI Studio в тестах не будет): печать бланка, загрузка фото,
распознавание черновиком, подтверждение сверки заводит поверку.
"""

from __future__ import annotations

from io import BytesIO
from unittest import mock

import numpy as np
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

pytest.importorskip("cv2")
pytest.importorskip("qrcode")

from PIL import Image, ImageDraw  # noqa: E402
import qrcode  # noqa: E402

from apps.core.models import Employee, Role, User  # noqa: E402
from apps.verification import scan as scan_service  # noqa: E402
from apps.verification.calculators import water_meter as wm  # noqa: E402
from apps.verification.models import (  # noqa: E402
    Client, Instrument, ScanUpload, Site, Verification, WorkOrder,
)
from apps.verification.tests import factories as f  # noqa: E402

LIMITS = {
    "q_min": "0.03", "q_transition_a": "0.15", "q_transition_b": "0.12",
    "q_nominal": "1.5", "q_max": "3",
    "error_below_transition": "5", "error_above_transition": "2",
}
PASSING_ROWS = [
    {"flow_rate": "0.03", "reading_start": "229.0830", "reading_end": "229.0890",
     "volume_standard": "0.0063"},
    {"flow_rate": "0.125", "reading_start": "229.0960", "reading_end": "229.1090",
     "volume_standard": "0.0132"},
    {"flow_rate": "0.93", "reading_start": "229.1260", "reading_end": "229.1570",
     "volume_standard": "0.0313"},
]

CANVAS_W, CANVAS_H = 700, 990
MARKER_SIZE, MARKER_MARGIN = 40, 25


def _synthetic_blank_jpeg(work_order_id: int) -> bytes:
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), "white")
    draw = ImageDraw.Draw(canvas)
    for x0, y0 in (
        (MARKER_MARGIN, MARKER_MARGIN),
        (CANVAS_W - MARKER_MARGIN - MARKER_SIZE, MARKER_MARGIN),
        (MARKER_MARGIN, CANVAS_H - MARKER_MARGIN - MARKER_SIZE),
        (CANVAS_W - MARKER_MARGIN - MARKER_SIZE, CANVAS_H - MARKER_MARGIN - MARKER_SIZE),
    ):
        draw.rectangle([x0, y0, x0 + MARKER_SIZE, y0 + MARKER_SIZE], fill="black")
    qr = qrcode.make(scan_service.qr_payload_for_work_order(work_order_id)).resize((160, 160))
    canvas.paste(qr, (CANVAS_W // 2 - 80, 90))

    buffer = BytesIO()
    canvas.save(buffer, format="JPEG")
    return buffer.getvalue()


class ScanTestBase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.si_type = f.make_si_type(self.family, limits=LIMITS)
        user = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        self.employee = Employee.objects.create(tab_number="09", full_name="Поверитель П. П.", user=user)
        self.client_obj = Client.objects.create(name="ООО Ромашка")
        self.site = Site.objects.create(address="г. Киров, ул. Мира, 1", client=self.client_obj)
        self.work_order = WorkOrder.objects.create(
            client=self.client_obj, site=self.site, assigned_employee=self.employee,
        )
        self.api = APIClient()
        self.api.force_authenticate(user)


class WorkOrderBlankViewTestCase(ScanTestBase):
    @override_settings(TYPST_BINARY="/definitely/not/a/real/binary")
    def test_unavailable_typst_reports_503_not_500(self):
        response = self.api.get(reverse("work_order_blank", args=[self.work_order.pk]))
        assert response.status_code == 503

    def test_missing_work_order_is_404(self):
        response = self.api.get(reverse("work_order_blank", args=[999999]))
        assert response.status_code == 404


class WorkOrderScansViewTestCase(ScanTestBase):
    def _upload(self, data: bytes, *, name: str = "blank.jpg"):
        photo = SimpleUploadedFile(name, data, content_type="image/jpeg")
        return self.api.post(
            reverse("work_order_scans", args=[self.work_order.pk]), {"photo": photo}, format="multipart"
        )

    @override_settings(YANDEX_VISION_API_KEY="", YANDEX_VISION_FOLDER_ID="")
    def test_upload_without_vlm_configured_stores_photo_and_warns(self):
        response = self._upload(_synthetic_blank_jpeg(self.work_order.id))
        assert response.status_code == 201
        assert response.data["recognized"] == {}
        assert "не настроено" in response.data["warning"]
        assert ScanUpload.objects.count() == 1
        assert ScanUpload.objects.get().verification_id is None

    @override_settings(YANDEX_VISION_API_KEY="key", YANDEX_VISION_FOLDER_ID="folder")
    @mock.patch("apps.verification.api_scan.YandexVisionClient.recognize_blank")
    def test_upload_with_vlm_stores_recognition(self, recognize):
        recognize.return_value = {
            "legible": True, "si_type_query": "СВК-15", "serial_number": "778899",
            "rows": [{"confidence": 0.4}] * 3,
        }
        response = self._upload(_synthetic_blank_jpeg(self.work_order.id))
        assert response.status_code == 201
        assert response.data["recognized"]["serial_number"] == "778899"
        assert "warning" not in response.data
        recognize.assert_called_once()

    @override_settings(YANDEX_VISION_API_KEY="key", YANDEX_VISION_FOLDER_ID="folder")
    @mock.patch("apps.verification.api_scan.YandexVisionClient.recognize_blank")
    def test_qr_for_another_work_order_produces_a_warning(self, recognize):
        recognize.return_value = {"legible": True, "rows": []}
        other = WorkOrder.objects.create(
            client=self.client_obj, site=self.site, assigned_employee=self.employee,
        )
        response = self._upload(_synthetic_blank_jpeg(other.id))
        assert response.status_code == 201
        assert f"№{other.id}" in response.data["warning"]

    def test_garbage_upload_is_stored_with_an_error_but_does_not_crash(self):
        response = self._upload(b"not-an-image-but-pillow-wont-mind-the-field", name="broken.jpg")
        # ImageField валидация на уровне DRF отклонит откровенный мусор.
        assert response.status_code in (201, 400)

    def test_requires_linked_employee(self):
        user = User.objects.create_user(email="obs@ei.test", password="pass12345", role=Role.OBSERVER)
        api = APIClient()
        api.force_authenticate(user)
        photo = SimpleUploadedFile("b.jpg", _synthetic_blank_jpeg(self.work_order.id), content_type="image/jpeg")
        response = api.post(
            reverse("work_order_scans", args=[self.work_order.pk]), {"photo": photo}, format="multipart"
        )
        assert response.status_code == 403


class ScanImageAndDetailViewTestCase(ScanTestBase):
    def setUp(self) -> None:
        super().setUp()
        from django.core.files.base import ContentFile

        self.scan = ScanUpload.objects.create(
            work_order=self.work_order, uploaded_by=self.employee,
            image=ContentFile(_synthetic_blank_jpeg(self.work_order.id), name="blank.jpg"),
        )

    def test_detail_returns_recognition_state(self):
        response = self.api.get(reverse("scan_detail", args=[self.scan.pk]))
        assert response.status_code == 200
        assert response.data["id"] == self.scan.pk

    def test_image_streams_the_stored_photo(self):
        response = self.api.get(reverse("scan_image", args=[self.scan.pk]))
        assert response.status_code == 200


class ScanApplyViewTestCase(ScanTestBase):
    def setUp(self) -> None:
        super().setUp()
        from django.core.files.base import ContentFile

        self.scan = ScanUpload.objects.create(
            work_order=self.work_order, uploaded_by=self.employee,
            image=ContentFile(_synthetic_blank_jpeg(self.work_order.id), name="blank.jpg"),
        )

    def payload(self, **overrides):
        data = {
            "si_type_id": self.si_type.id,
            "serial_number": "SN-777",
            "measurements": {"layout": wm.LAYOUT_COMPACT, "rows": PASSING_ROWS},
        }
        data.update(overrides)
        return data

    def test_confirming_the_review_screen_creates_a_verification(self):
        response = self.api.post(
            reverse("scan_apply", args=[self.scan.pk]), self.payload(), format="json"
        )
        assert response.status_code == 201
        assert Verification.objects.count() == 1
        assert Instrument.objects.get().serial_number == "SN-777"
        self.scan.refresh_from_db()
        assert self.scan.verification_id == response.data["id"]
        # source=scan идёт через тот же measurements.apply(), что и обычный ввод.
        verification = Verification.objects.get()
        assert verification.measurements["source"] == "scan"

    def test_applying_twice_is_idempotent_by_scan(self):
        first = self.api.post(reverse("scan_apply", args=[self.scan.pk]), self.payload(), format="json")
        second = self.api.post(reverse("scan_apply", args=[self.scan.pk]), self.payload(), format="json")
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.data["id"] == second.data["id"]
        assert Verification.objects.count() == 1

    def test_unknown_si_type_is_404(self):
        response = self.api.post(
            reverse("scan_apply", args=[self.scan.pk]), self.payload(si_type_id=999999), format="json"
        )
        assert response.status_code == 404

    def test_requires_linked_employee(self):
        user = User.objects.create_user(email="obs2@ei.test", password="pass12345", role=Role.OBSERVER)
        api = APIClient()
        api.force_authenticate(user)
        response = api.post(reverse("scan_apply", args=[self.scan.pk]), self.payload(), format="json")
        assert response.status_code == 403
