"""Обработка фото бланка: QR и выравнивание по реперным меткам.

Фото здесь не с телефона, а собранные в PIL «синтетические» — ровно
настолько, чтобы проверить сами алгоритмы (детектор меток, перспективная
коррекция, разбор QR), а не качество распознавания на реальной фотографии.
"""

from __future__ import annotations

import numpy as np
import pytest
from django.test import SimpleTestCase

pytest.importorskip("cv2")
pytest.importorskip("qrcode")

from PIL import Image, ImageDraw  # noqa: E402
import qrcode  # noqa: E402

from apps.verification import scan  # noqa: E402

CANVAS_W, CANVAS_H = 700, 990
MARKER_SIZE = 40
MARKER_MARGIN = 25


def _to_bgr(image: Image.Image) -> np.ndarray:
    return np.array(image.convert("RGB"))[:, :, ::-1].copy()


def _synthetic_blank(work_order_id: int = 42, *, skew: bool = False) -> np.ndarray:
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), "white")
    draw = ImageDraw.Draw(canvas)
    for x0, y0 in (
        (MARKER_MARGIN, MARKER_MARGIN),
        (CANVAS_W - MARKER_MARGIN - MARKER_SIZE, MARKER_MARGIN),
        (MARKER_MARGIN, CANVAS_H - MARKER_MARGIN - MARKER_SIZE),
        (CANVAS_W - MARKER_MARGIN - MARKER_SIZE, CANVAS_H - MARKER_MARGIN - MARKER_SIZE),
    ):
        draw.rectangle([x0, y0, x0 + MARKER_SIZE, y0 + MARKER_SIZE], fill="black")

    qr = qrcode.make(scan.qr_payload_for_work_order(work_order_id)).resize((160, 160))
    canvas.paste(qr, (CANVAS_W // 2 - 80, 90))

    image = _to_bgr(canvas)
    if not skew:
        return image

    # Небольшая перспективная деформация — имитирует фото под углом.
    import cv2

    src = np.array(
        [[0, 0], [CANVAS_W, 0], [CANVAS_W, CANVAS_H], [0, CANVAS_H]], dtype="float32"
    )
    dst = np.array(
        [[15, 30], [CANVAS_W - 5, 0], [CANVAS_W - 25, CANVAS_H - 10], [10, CANVAS_H - 5]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (CANVAS_W, CANVAS_H), borderValue=(255, 255, 255))


class QrPayloadTestCase(SimpleTestCase):
    def test_round_trips_work_order_id(self):
        payload = scan.qr_payload_for_work_order(17)
        assert payload == "eidoc:wo:17"
        assert scan.work_order_id_from_qr(payload) == 17

    def test_unrelated_text_is_not_a_work_order(self):
        assert scan.work_order_id_from_qr("https://example.com") is None
        assert scan.work_order_id_from_qr("") is None


class DecodeQrTestCase(SimpleTestCase):
    def test_decodes_qr_from_synthetic_blank(self):
        image = _synthetic_blank(work_order_id=17)
        data = scan.decode_qr(image)
        assert data == "eidoc:wo:17"

    def test_no_qr_on_a_blank_page(self):
        image = _to_bgr(Image.new("RGB", (400, 400), "white"))
        assert scan.decode_qr(image) is None


class DecodeImageTestCase(SimpleTestCase):
    def test_decodes_jpeg_bytes(self):
        raw = scan.encode_jpeg(_synthetic_blank())
        image = scan.decode_image(raw)
        assert image.shape[:2] == (CANVAS_H, CANVAS_W)

    def test_garbage_bytes_raise_scan_error(self):
        with pytest.raises(scan.ScanError):
            scan.decode_image(b"not an image")


class FindMarkersTestCase(SimpleTestCase):
    def test_finds_four_markers_on_flat_photo(self):
        image = _synthetic_blank()
        corners = scan.find_markers(image)
        assert corners.shape == (4, 2)
        # TL заметно левее и выше BR.
        tl, tr, br, bl = corners
        assert tl[0] < tr[0] and tl[1] < bl[1]
        assert br[0] > bl[0] and br[1] > tr[1]

    def test_raises_when_markers_are_missing(self):
        image = _to_bgr(Image.new("RGB", (400, 400), "white"))
        with pytest.raises(scan.ScanError):
            scan.find_markers(image)


class AlignTestCase(SimpleTestCase):
    def test_align_normalises_a_skewed_photo_to_canvas_size(self):
        skewed = _synthetic_blank(work_order_id=9, skew=True)
        aligned = scan.align(skewed)
        assert aligned.shape[:2] == (scan.CANVAS_HEIGHT_PX, scan.CANVAS_WIDTH_PX)
        # QR должен остаться читаемым после выравнивания.
        assert scan.decode_qr(aligned) == "eidoc:wo:9"
