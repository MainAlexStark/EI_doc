"""Собрать встроенный шаблон договора (backend/apps/hub/templates_docx/contract_default.docx).

    python tools/build_contract_template.py

Шаблон — обычный .docx с полями docxtpl. Юрист может открыть его в Word,
поправить текст и загрузить как свой «Шаблон договора» в админке, не трогая
поля в фигурных скобках. Список полей — docs/contracts.md.
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

OUT = Path(__file__).resolve().parent.parent / "backend/apps/hub/templates_docx/contract_default.docx"

doc = Document()
style = doc.styles["Normal"]
style.font.name = "Times New Roman"
style.font.size = Pt(11)


def para(text: str, *, bold: bool = False, center: bool = False, size: int | None = None):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    if size:
        run.font.size = Pt(size)
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


para("ДОГОВОР № {{ number }}", bold=True, center=True, size=13)
para("на оказание услуг по поверке средств измерений", center=True)
para("г. Киров\t\t\t\t\t\t\t\t{{ date }}")
para(
    "{{ org.full_name }}, именуемое в дальнейшем «Исполнитель», в лице директора "
    "{{ org.director or '____________' }}, действующего на основании Устава, с одной стороны, и "
    "{% if client.is_org %}{{ client.name }}, ИНН {{ client.inn or '____________' }}, именуемое{% else %}"
    "{{ client.name }}, именуемый(ая){% endif %} в дальнейшем «Заказчик», с другой стороны, "
    "заключили настоящий договор о нижеследующем."
)
para("1. Предмет договора", bold=True)
para(
    "1.1. Исполнитель обязуется выполнить поверку средств измерений (СИ) Заказчика, "
    "а Заказчик — принять и оплатить услуги. Поверка выполняется по адресу: {{ address }}."
)
para("1.2. Перечень СИ и стоимость:")

table = doc.add_table(rows=3, cols=4)
table.style = "Table Grid"
for cell, text in zip(table.rows[0].cells, ["Наименование", "Кол-во", "Цена, ₽", "Сумма, ₽"]):
    cell.text = text
table.rows[1].cells[0].text = "{%tr for item in items %}"
row = table.rows[2].cells
row[0].text = "{{ item.name }}"
row[1].text = "{{ item.quantity }}"
row[2].text = "{{ item.price }}"
row[3].text = "{{ item.subtotal }}"
end = table.add_row().cells
end[0].text = "{%tr endfor %}"

para("Итого: {{ total }} руб. ({{ total_words }}), НДС не облагается.", bold=True)
para("2. Порядок оказания услуг", bold=True)
para(
    "2.1. Дата выполнения работ согласовывается сторонами{% if date_planned %} и предварительно "
    "назначена на {{ date_planned }}{% endif %}."
)
para(
    "2.2. Результаты поверки передаются в Федеральный информационный фонд по обеспечению единства "
    "измерений. По требованию Заказчика выдаётся протокол поверки."
)
para("2.3. Средство измерений, признанное непригодным, получает извещение о непригодности.")
para("3. Оплата", bold=True)
para("3.1. Оплата производится в день выполнения работ наличными или по безналичному расчёту.")
para("4. Персональные данные", bold=True)
para(
    "4.1. Заказчик даёт согласие на обработку персональных данных, необходимых для исполнения договора "
    "и передачи сведений о поверке в ФИФ ОЕИ, в соответствии с Федеральным законом № 152-ФЗ. "
    "Политика обработки персональных данных Исполнителя: {{ privacy_url }}."
)
para("5. Реквизиты и подписи сторон", bold=True)

req = doc.add_table(rows=1, cols=2)
left, right = req.rows[0].cells
left.text = (
    "Исполнитель:\n{{ org.name }}\nИНН {{ org.inn or '__________' }} КПП {{ org.kpp or '_________' }}\n"
    "ОГРН {{ org.ogrn or '_____________' }}\n{{ org.address }}\n"
    "р/с {{ org.account or '____________________' }}\n{{ org.bank or '' }} БИК {{ org.bik or '_________' }}\n"
    "{% if org.accreditation %}Аттестат аккредитации {{ org.accreditation }}\n{% endif %}"
    "\n_______________ / {{ org.director or '____________' }}"
)
right.text = (
    "Заказчик:\n{{ client.name }}\n{% if client.inn %}ИНН {{ client.inn }}\n{% endif %}"
    "{% if client.phone %}тел. {{ client.phone }}\n{% endif %}{% if client.email %}{{ client.email }}\n{% endif %}"
    "{{ address }}\n\n_______________ / {{ client.short_name }}"
)

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUT)
print(OUT)
