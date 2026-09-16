"""Сборка данных протокола и рендер PDF через Typst.

Шаблон — текстовый файл в репозитории (`typst/water_meter_v1.typ`), вёрстка
повторяет прежний документ из `.xlsm`. Данные подаются отдельным `data.json`,
поэтому шаблон ничего не считает и в него не попадает логика.

Числа форматируются здесь, а не в шаблоне: в протоколе принята запятая как
разделитель и фиксированное число знаков по каждой графе.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

import qrcode
from django.conf import settings
from django.utils import timezone

from apps.verification.calculators import water_meter as wm

TEMPLATE_DIR = Path(__file__).parent / "typst"
DEFAULT_TEMPLATE = "water_meter_v1.typ"


def typst_binary() -> str:
    """Путь к бинарнику. В образе он ставится Dockerfile'ом, локально — из PATH."""
    configured = getattr(settings, "TYPST_BINARY", "") or ""
    return configured or shutil.which("typst") or "typst"


def typst_available() -> bool:
    return shutil.which(typst_binary()) is not None or Path(typst_binary()).exists()

ORG = {
    "legal_form": "Общество с ограниченной ответственностью",
    "name": '"ЕДИНИЦА ИЗМЕРЕНИЯ"',
    "address_legal": "610008, г. Киров, ул. Гагарина, д. 20, кв.62",
    "address_actual": (
        "610027, Россия, Кировская область, город Киров, "
        "улица Красноармейская, дом 43А, помещение 1,21."
    ),
}

CONCLUSION_OK = (
    "На основании результатов поверки СИ соответствует установленным "
    "метрологическим и техническим требованиям и пригодно к применению."
)
CONCLUSION_FAIL = (
    "На основании результатов поверки СИ не соответствует установленным "
    "метрологическим и техническим требованиям и не пригодно к применению."
)

CONDITION_NOTES = {
    "temperature": "от 5-50°С",
    "humidity": "от 30-95%",
    "pressure": "от 84-106 кПа",
    "water": "от 5-40°С для СХВ, от 40-90°С для СГВ,\nот 5-90°С для универсальных счётчиков",
}


class RenderError(RuntimeError):
    pass


def ru(value, digits: int) -> str:
    """Число в том виде, в каком оно печатается в протоколе: 0,030."""
    if value is None or value == "":
        return ""
    quantized = Decimal(str(value)).quantize(Decimal(1).scaleb(-digits))
    return f"{quantized:f}".replace(".", ",")


def _litres(value: Decimal) -> str:
    """Расход в л/ч без лишних нулей: 0.03 м³/ч → 30."""
    litres = (Decimal(str(value)) * 1000).normalize()
    return f"{litres:f}".replace(".", ",")


def mode_labels(limits: wm.MeterLimits) -> dict[str, str]:
    """Подписи режимов — те же формулировки, что в прежнем шаблоне."""
    q_min_a, q_min_b = limits.q_min * 2, limits.q_min
    q_t_a, q_t_b = limits.q_transition_a * Decimal("1.1"), limits.q_transition_b * Decimal("1.1")

    return {
        wm.MODE_MIN: (
            "Измерения на минимальном расходе Qнаим, равном\n"
            f"({_litres(q_min_a)} + {_litres(q_min_a / 10)}) л/ч (класс А)\n"
            f"({_litres(q_min_b)} + {_litres(q_min_b / 10)}) л/ч (класс В)"
        ),
        wm.MODE_TRANSITION: (
            "Измерения на  расходе 1,1*Qп, равном\n"
            f"({_litres(q_t_a)} ± {_litres(q_t_a / 10)}) л/ч (класс А)\n"
            f"({_litres(q_t_b)} ± {_litres(q_t_b / 10)}) л/ч (класс В)"
        ),
        wm.MODE_MAX: "Измерения на расходе Qнаиб , л/ч",
    }


def ranges_block(limits: wm.MeterLimits) -> list[str]:
    """Блок «Диапазон измерения…» — дословно как в прежнем протоколе."""
    q_max = ru(limits.q_max, 0)
    return [
        f"от {ru(limits.q_min * 2, 2)} м3/ч до {q_max} м3/ч  (класс А)",
        f"от {ru(limits.q_min, 2)} м3/ч до {q_max} м3/ч  (класс В)",
        "Пределы допускаемой относительной погрешности в диапазоне расходов:",
        "класс А:",
        f"от {ru(limits.q_min * 2, 2)} м3/ч до {ru(limits.q_transition_a, 2)} м3/ч : "
        f"± {ru(limits.error_below_transition, 0)} %",
        f"от {ru(limits.q_transition_a, 2)} м3/ч до {q_max} м3/ч : "
        f"± {ru(limits.error_above_transition, 0)} %",
        "Пределы допускаемой относительной погрешности в диапазоне расходов:",
        "класс В:",
        f"от {ru(limits.q_min, 2)} м3/ч до {ru(limits.q_transition_b, 2)} м3/ч : "
        f"± {ru(limits.error_below_transition, 0)} %",
        f"от {ru(limits.q_transition_b, 2)} м3/ч до {q_max} м3/ч : "
        f"± {ru(limits.error_above_transition, 0)} %",
    ]


def build_context(verification, *, number: str) -> dict:
    """Собрать данные протокола из поверки."""
    from apps.verification.measurements import limits_for

    instrument = verification.instrument
    si_type = instrument.si_type
    limits = limits_for(verification)
    entered = verification.measurements or {}
    computed = verification.results or {}

    if not computed.get("rows"):
        raise RenderError("В поверке нет результатов измерений — печатать нечего")

    labels = mode_labels(limits)
    stored_rows = {row["row"]: row for row in entered.get("rows", [])}

    rows = []
    for index, row in enumerate(computed["rows"], start=1):
        source = stored_rows.get(index, {})
        rows.append(
            {
                "mode_label": labels.get(row["mode"], ""),
                "flow_rate": ru(row["flow_rate"], 3),
                "reading_start": ru(source.get("reading_start"), 3),
                "reading_end": ru(source.get("reading_end"), 3),
                "volume_meter": ru(row["volume_meter"], 3),
                "volume_standard": ru(row["volume_standard"], 4),
                "error_pct": ru(row["error_pct"], 1),
                "limit_pct": ru(row["limit_pct"], 0),
            }
        )

    conditions = [
        {"label": "Температура окружающей среды, °С",
         "start": ru(verification.temperature, 0), "end": ru(verification.temperature, 1),
         "note": CONDITION_NOTES["temperature"]},
        {"label": "Влажность окружающей среды, %",
         "start": ru(verification.humidity, 1), "end": ru(verification.humidity, 1),
         "note": CONDITION_NOTES["humidity"]},
        {"label": "Атмосферное давление,  кПа",
         "start": ru(verification.pressure, 0), "end": ru(verification.pressure, 1),
         "note": CONDITION_NOTES["pressure"]},
        {"label": "Температура поверочной жидкости-воды, °С",
         "start": ru(entered.get("water_temperature"), 0),
         "end": ru(entered.get("water_temperature"), 0),
         "note": CONDITION_NOTES["water"]},
    ]

    checks = [f"{index}. {label}: соответствует." for index, label in enumerate(wm.CHECKS.values(), 1)]

    return {
        "org": ORG,
        "number": number,
        "subject": (
            f"периодической поверки СИ - счетчик воды {si_type.name}, "
            f"год изготовления {instrument.manufacture_year}, "
            f"заводской № {instrument.serial_number}"
        ),
        "registry_number": si_type.registry_number,
        "owner": str(instrument.owner) if instrument.owner_id else "Частное лицо",
        "place": str(instrument.site) if instrument.site_id else "",
        "ranges": ranges_block(limits),
        "conditions": conditions,
        "standards": [str(standard) for standard in verification.standards.all()],
        "method": str(verification.method) if verification.method_id else "",
        "checks": checks,
        "meter_class": entered.get("meter_class", wm.CLASS_B),
        "rows": rows,
        "pulse_weight": (entered.get("pulse_weight") or "").replace(".", ","),
        "conclusion": CONCLUSION_OK if verification.suitable else CONCLUSION_FAIL,
        "verifier": verification.verifier.short_name or verification.verifier.full_name,
        "date": verification.verified_at.strftime("%d.%m.%Y"),
    }


def render_pdf(
    context: dict, *, template: str | Path = DEFAULT_TEMPLATE,
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    """Сверстать PDF по данным протокола.

    ``extra_files`` — дополнительные бинарные файлы рядом с template.typ/
    data.json в рабочем каталоге (например, qr.png для бланка) — шаблон
    читает их по относительному пути через image().
    """
    source = Path(template)
    if not source.is_absolute():
        source = TEMPLATE_DIR / source
    if not source.exists():
        raise RenderError(f"Шаблон не найден: {source}")

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        (work / "template.typ").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        (work / "data.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        for name, data in (extra_files or {}).items():
            (work / name).write_bytes(data)
        output = work / "protocol.pdf"

        try:
            completed = subprocess.run(
                [typst_binary(), "compile", "--root", str(work),
                 str(work / "template.typ"), str(output)],
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError as exc:
            raise RenderError(
                "Не найден бинарник typst. В образе он ставится Dockerfile'ом; "
                "локально — https://github.com/typst/typst/releases"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RenderError("Typst не уложился в минуту") from exc

        if completed.returncode != 0:
            raise RenderError(f"Typst не смог собрать протокол:\n{completed.stderr.strip()}")

        return output.read_bytes()


def render_for(verification, *, number: str) -> bytes:
    return render_pdf(build_context(verification, number=number))


def template_source(name: str = DEFAULT_TEMPLATE) -> str:
    """Исходник шаблона — для загрузки в catalog.ProtocolTemplate."""
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Печатный бланк с QR (второй срез офлайна — см. claude/scans.md)
# ---------------------------------------------------------------------------
BLANK_TEMPLATE = "blank_water_meter_v1.typ"


def build_blank_context(work_order) -> dict:
    """Данные для печати пустого бланка — сам он ничего не считает и не хранит."""
    from apps.verification import measurements as measurements_service

    rows = [
        {"label": wm.MODES[mode]["label"], "seconds": wm.MODES[mode]["seconds"]}
        for mode in wm.LAYOUTS[wm.LAYOUT_COMPACT]
    ]
    return {
        "org": ORG,
        "work_order_number": work_order.id,
        "client": str(work_order.client),
        "site": str(work_order.site),
        "date": timezone.now().strftime("%d.%m.%Y"),
        "checks": list(wm.CHECKS.values()),
        "rows": rows,
        "common_unsuitability_reasons": measurements_service.COMMON_UNSUITABILITY_REASONS,
    }


def render_blank(work_order, *, copies: int = 1) -> bytes:
    """PDF печатного бланка: QR наряда + реперные метки + клетки под ручной ввод.

    Один бланк — один счётчик (заранее не известно, сколько их будет и какие
    — см. WorkOrder/RequestItem), поэтому QR кодирует только наряд
    (``apps.verification.scan.qr_payload_for_work_order``), не конкретное
    СИ. Если счётчиков несколько, страница просто повторяется ``copies`` раз
    — различать экземпляры бланка друг от друга не требуется, каждое фото
    заводит свою поверку через apps.verification.api_scan.

    Реперные метки на bordare листа — под perspective-align на фото, см.
    apps.verification.scan.align(); их геометрия (отступ, размер) должна
    остаться согласованной с scan.CANVAS_*/MARKER_MARGIN_PX при правке.
    """
    from apps.verification import scan as scan_service

    copies = max(1, min(int(copies), 20))
    qr_image = qrcode.make(scan_service.qr_payload_for_work_order(work_order.id))
    qr_buffer = io.BytesIO()
    qr_image.save(qr_buffer, format="PNG")

    context = build_blank_context(work_order)
    context["copies"] = copies

    return render_pdf(context, template=BLANK_TEMPLATE, extra_files={"qr.png": qr_buffer.getvalue()})
