"""Обработка фото бумажного бланка: декодирование QR и выравнивание по меткам.

Бланк печатается ``render.render_blank`` — там же реперные метки: четыре
закрашенных квадрата по углам печатной области, и QR с идентификатором
наряда в верхнем углу. Здесь метки находят на фото (снятом под углом, при
обычном освещении телефоном) и считают перспективное преобразование к тому
же холсту, на котором бланк печатался, — дальше распознаванию VLM отдаётся
уже выровненное изображение, это заметно поднимает точность.

Размеры холста (``CANVAS_*``) должны совпадать с тем, что задаёт
``render.render_blank`` — меняются оба места вместе.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover — opencv не поставлен в окружении
    cv2 = None  # type: ignore[assignment]

# A4 при 200 DPI — тот же холст, на который рендерится бланк.
CANVAS_WIDTH_PX = 1654
CANVAS_HEIGHT_PX = 2339

# Отступ реперных меток от края холста — должен совпадать с render.py.
MARKER_MARGIN_PX = 40


# Префикс QR наряда — печатается render.render_blank(), читается здесь.
# Строкой, не голым числом: обычный QR-ридер (телефон, не наше приложение)
# должен видеть осмысленный текст, а не случайное число.
QR_PREFIX = "eidoc:wo:"


def qr_payload_for_work_order(work_order_id: int) -> str:
    return f"{QR_PREFIX}{work_order_id}"


def work_order_id_from_qr(data: str) -> int | None:
    if not data.startswith(QR_PREFIX):
        return None
    tail = data[len(QR_PREFIX):]
    return int(tail) if tail.isdigit() else None


class ScanError(ValueError):
    """Фото не годится для распознавания — показать поверителю, а не падать."""


def opencv_available() -> bool:
    return cv2 is not None


def _require_cv2() -> None:
    if cv2 is None:
        raise ScanError(
            "На сервере не установлен opencv-python-headless — обработка фото недоступна"
        )


def decode_image(data: bytes) -> np.ndarray:
    """Байты файла (jpeg/png) → массив BGR для OpenCV."""
    _require_cv2()
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ScanError("Не удалось прочитать изображение — файл повреждён или не фото")
    return image


def decode_qr(image: np.ndarray) -> str | None:
    """Данные QR-кода на фото, если он вообще нашёлся."""
    _require_cv2()
    detector = cv2.QRCodeDetector()
    data, _points, _straight = detector.detectAndDecode(image)
    return data or None


def _order_corners(points: np.ndarray) -> np.ndarray:
    """4 точки → TL, TR, BR, BL по сумме/разности координат.

    Стандартный приём для перспективной коррекции: у верхней левой точки
    минимальна сумма x+y, у нижней правой — максимальна; у верхней правой
    минимальна разность x-y, у нижней левой — максимальна.
    """
    s = points.sum(axis=1)
    d = np.diff(points, axis=1).ravel()
    tl = points[np.argmin(s)]
    br = points[np.argmax(s)]
    tr = points[np.argmin(d)]
    bl = points[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype="float32")


def find_markers(image: np.ndarray) -> np.ndarray:
    """Найти 4 реперные метки — закрашенные квадраты по углам кадра.

    Возвращает их центры, упорядоченные TL/TR/BR/BL. Кидает ScanError, если
    подходящих контуров нашлось не ровно 4 — лучше попросить переснять, чем
    выравнивать по случайному совпадению.
    """
    _require_cv2()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    h, w = gray.shape[:2]
    frame_area = w * h
    candidates: list[tuple[float, float, float]] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < frame_area * 0.0004 or area > frame_area * 0.03:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, bw, bh = cv2.boundingRect(approx)
        if bh == 0 or not (0.7 <= bw / bh <= 1.3):
            continue
        candidates.append((area, x + bw / 2.0, y + bh / 2.0))

    if len(candidates) < 4:
        raise ScanError(
            f"Не удалось найти реперные метки на фото (нашлось {len(candidates)} из 4) — "
            "переснимите бланк целиком, без бликов и обрезанных углов"
        )

    # Метки печатаются заметно крупнее фонового шума и текста — берём 4 крупнейших.
    candidates.sort(key=lambda item: item[0], reverse=True)
    points = np.array([[cx, cy] for _, cx, cy in candidates[:4]], dtype="float32")
    return _order_corners(points)


def align(image: np.ndarray) -> np.ndarray:
    """Перспективная коррекция фото к холсту бланка по реперным меткам."""
    _require_cv2()
    src = find_markers(image)  # уже упорядочены TL/TR/BR/BL внутри find_markers
    m = MARKER_MARGIN_PX
    dst = np.array(
        [
            [m, m],
            [CANVAS_WIDTH_PX - m, m],
            [CANVAS_WIDTH_PX - m, CANVAS_HEIGHT_PX - m],
            [m, CANVAS_HEIGHT_PX - m],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX))


def encode_jpeg(image: np.ndarray, *, quality: int = 90) -> bytes:
    _require_cv2()
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ScanError("Не удалось закодировать выровненное изображение")
    return buffer.tobytes()
