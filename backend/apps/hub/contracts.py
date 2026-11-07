"""Договоры с клиентами: .docx по шаблону docxtpl из заявки или наряда.

Истина — в базе (заявка, клиент, приборы, цены); договор — производный файл.
Выпущенный договор не перезаписывается: правка — новый договор с новым
номером. Номера сквозные в пределах года (Д-2026-0001), выдаются под
блокировкой, как номера протоколов.
"""

from __future__ import annotations

import hashlib
import io
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.hub.models import Contract, ContractTemplate, Request
from apps.verification.models import Client, ClientKind, WorkOrder

DEFAULT_TEMPLATE = Path(__file__).parent / "templates_docx" / "contract_default.docx"


class ContractError(ValueError):
    """Сообщение для человека."""


# ---------------------------------------------------------------------------
# Сумма прописью
# ---------------------------------------------------------------------------
_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["", "одна", "две"] + _UNITS[3:]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят",
         "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот",
             "девятьсот"]
_SCALES = [  # (формы, женский род)
    (("", "", ""), False),
    (("тысяча", "тысячи", "тысяч"), True),
    (("миллион", "миллиона", "миллионов"), False),
]


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


def _triad(n: int, feminine: bool) -> list[str]:
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        words.append(_TENS[rest // 10])
        words.append((_UNITS_F if feminine else _UNITS)[rest % 10])
    return [w for w in words if w]


def rubles_in_words(amount: Decimal) -> str:
    amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rubles, kopecks = int(amount), int((amount - int(amount)) * 100)
    if rubles == 0:
        words = ["ноль"]
    else:
        words, n, scale = [], rubles, 0
        while n:
            triad = n % 1000
            if triad:
                forms, feminine = _SCALES[scale]
                part = _triad(triad, feminine)
                if scale:
                    part.append(_plural(triad, forms))
                words = part + words
            n //= 1000
            scale += 1
    text = " ".join(words)
    text = text[0].upper() + text[1:]
    return f"{text} {_plural(rubles, ('рубль', 'рубля', 'рублей'))} {kopecks:02d} {_plural(kopecks, ('копейка', 'копейки', 'копеек'))}"


# ---------------------------------------------------------------------------
# Данные договора
# ---------------------------------------------------------------------------
def _money(value: Decimal) -> str:
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")


def _short_name(full: str) -> str:
    parts = full.split()
    if len(parts) >= 2 and all(p[:1].isupper() for p in parts[:3]):
        return f"{parts[0]} " + "".join(f"{p[0]}." for p in parts[1:3])
    return full


def client_for_request(request: Request) -> Client:
    """Клиент из заявки: по телефону, иначе новый."""
    if request.contact_phone:
        found = Client.objects.filter(phone=request.contact_phone).first()
        if found:
            return found
    return Client.objects.create(name=request.contact_name, phone=request.contact_phone,
                                 email=request.contact_email)


def build_context(*, client: Client, request: Request | None, work_order: WorkOrder | None, number: str) -> dict:
    items = []
    if request is not None:
        for item in request.items.select_related("family"):
            items.append({"name": f"Поверка: {item.family.name}", "quantity": item.quantity,
                          "price": _money(item.unit_price), "subtotal": _money(item.subtotal),
                          "_subtotal": item.subtotal})
    total = sum((i["_subtotal"] for i in items), Decimal("0"))
    if not items and request is not None and request.estimated_price:
        total = request.estimated_price
        items.append({"name": "Поверка средств измерений", "quantity": 1, "price": _money(total),
                      "subtotal": _money(total), "_subtotal": total})
    for item in items:
        item.pop("_subtotal", None)

    address = ""
    planned = None
    if work_order is not None:
        address = work_order.site.address
        planned = work_order.scheduled_date
    elif request is not None:
        address = request.address
        planned = request.desired_date

    return {
        "number": number,
        "date": timezone.localdate().strftime("%d.%m.%Y"),
        "org": dict(getattr(settings, "ORG_REQUISITES", {})),
        "client": {
            "name": client.name, "inn": client.inn, "phone": client.phone, "email": client.email,
            "is_org": client.kind == ClientKind.ORG, "short_name": _short_name(client.name),
        },
        "address": address or "____________________",
        "date_planned": planned.strftime("%d.%m.%Y") if planned else "",
        "items": items,
        "total": _money(total),
        "total_words": rubles_in_words(total),
        "_total": total,
        "privacy_url": getattr(settings, "PRIVACY_POLICY_URL", "") or "/privacy/",
    }


def pick_template(client: Client) -> ContractTemplate | None:
    qs = ContractTemplate.objects.filter(is_active=True)
    return qs.filter(client_kind=client.kind).first() or qs.filter(client_kind="").first()


def render_docx(template_bytes: bytes, context: dict) -> bytes:
    from docxtpl import DocxTemplate

    doc = DocxTemplate(io.BytesIO(template_bytes))
    doc.render({k: v for k, v in context.items() if not k.startswith("_")}, autoescape=True)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


@transaction.atomic
def issue(*, request: Request | None = None, work_order: WorkOrder | None = None, user=None) -> Contract:
    if request is None and work_order is None:
        raise ContractError("Договор выпускается из заявки или наряда")
    if work_order is not None and request is None:
        request = work_order.request
    client = work_order.client if work_order is not None else client_for_request(request)

    year = timezone.localdate().year
    # Блокировка последней строки года — два менеджера не получат один номер
    # (первый договор года страхует уникальный индекс contract_unique_number).
    last = Contract.objects.select_for_update().filter(year=year).order_by("-seq").first()
    seq = (last.seq if last else 0) + 1
    number = f"Д-{year}-{seq:04d}"

    template = pick_template(client)
    if template is not None:
        with template.file.open("rb") as handle:
            source = handle.read()
    else:
        source = DEFAULT_TEMPLATE.read_bytes()

    context = build_context(client=client, request=request, work_order=work_order, number=number)
    try:
        payload = render_docx(source, context)
    except Exception as exc:  # битый шаблон или поле — понятная ошибка вместо 500
        raise ContractError(f"Шаблон договора не заполнился: {exc}") from exc

    contract = Contract(year=year, seq=seq, client=client, request=request, work_order=work_order,
                        template=template, total=context["_total"],
                        created_by=user if getattr(user, "pk", None) else None)
    contract.sha256 = hashlib.sha256(payload).hexdigest()
    contract.file.save(f"{number}.docx", ContentFile(payload), save=False)
    contract.save()
    return contract
