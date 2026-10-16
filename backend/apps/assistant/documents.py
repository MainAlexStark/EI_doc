"""Документы, которые создаёт ассистент: Word, Excel, PDF, Markdown, CSV.

Один и тот же набор блоков (заголовок, абзац, поля «подпись — значение»,
таблица, список, подписи) превращается и в .docx (python-docx — чтобы человек
мог дописать руками), и в PDF (Typst, тот же движок, что у протоколов
поверки). Excel собирается из листов со строками.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

TEMPLATE = Path(__file__).parent / "typst" / "document_v1.typ"

BLOCK_TYPES = ("heading", "paragraph", "fields", "table", "list", "signatures", "pagebreak", "spacer")

ORG_HEADER = [
    "Общество с ограниченной ответственностью «ЕДИНИЦА ИЗМЕРЕНИЯ»",
    "610027, Россия, Кировская область, город Киров, улица Красноармейская, дом 43А, помещение 1,21",
]


class DocumentError(ValueError):
    pass


def safe_filename(name: str, ext: str) -> str:
    stem = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", (name or "документ").strip())[:120] or "документ"
    stem = re.sub(r"\.(docx|xlsx|pdf|md|csv|txt)$", "", stem, flags=re.I)
    return f"{stem}.{ext}"


def clean_blocks(blocks) -> list[dict]:
    if not isinstance(blocks, list) or not blocks:
        raise DocumentError("Нужен непустой список blocks")
    result = []
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise DocumentError(f"blocks[{i}] — не объект")
        kind = block.get("type", "paragraph")
        if kind not in BLOCK_TYPES:
            raise DocumentError(f"blocks[{i}]: неизвестный тип «{kind}», можно {', '.join(BLOCK_TYPES)}")
        block = dict(block, type=kind)
        if kind in ("heading", "paragraph"):
            block["text"] = str(block.get("text", ""))
        elif kind == "fields":
            rows = block.get("rows") or []
            block["rows"] = [
                {"label": str(r.get("label", "")), "value": str(r.get("value", "")), "note": str(r.get("note", ""))}
                for r in rows if isinstance(r, dict)
            ]
        elif kind == "table":
            columns = [str(c) for c in (block.get("columns") or [])]
            if not columns:
                raise DocumentError(f"blocks[{i}]: у таблицы нет columns")
            rows = []
            for row in block.get("rows") or []:
                row = [("" if c is None else str(c)) for c in (row if isinstance(row, list) else [row])]
                rows.append((row + [""] * len(columns))[: len(columns)])
            block["columns"], block["rows"] = columns, rows
            widths = block.get("widths")
            if not (isinstance(widths, list) and len(widths) == len(columns)):
                block.pop("widths", None)
            else:
                block["widths"] = [float(w) if float(w) > 0 else 1.0 for w in widths]
        elif kind == "list":
            block["items"] = [str(x) for x in (block.get("items") or [])]
        elif kind == "signatures":
            block["rows"] = [
                {"role": str(r.get("role", "")), "name": str(r.get("name", ""))}
                for r in (block.get("rows") or []) if isinstance(r, dict)
            ]
        result.append(block)
    return result


def document_context(spec: dict) -> dict:
    header = spec.get("header")
    if header == "org" or header is True:
        header = {"lines": ORG_HEADER, "align": "center"}
    elif isinstance(header, list):
        header = {"lines": [str(x) for x in header], "align": "center"}
    elif isinstance(header, dict):
        header = {"lines": [str(x) for x in header.get("lines") or []], "align": header.get("align", "center")}
    else:
        header = None
    return {
        "title": str(spec.get("title") or ""),
        "subtitle": str(spec.get("subtitle") or ""),
        "header": header,
        "draft": bool(spec.get("draft", False)),
        "footer": str(spec.get("footer") or ""),
        "blocks": clean_blocks(spec.get("blocks")),
    }


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def render_pdf(spec: dict) -> bytes:
    from apps.verification.render import render_pdf as typst_render

    return typst_render(document_context(spec), template=TEMPLATE)


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------
def render_docx(spec: dict) -> bytes:
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    ctx = document_context(spec)
    document = docx.Document()
    section = document.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21)
    section.left_margin, section.right_margin = Cm(2), Cm(1.5)
    section.top_margin, section.bottom_margin = Cm(1.5), Cm(1.8)

    style = document.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(3)

    align_map = {
        "center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT,
    }

    def para(text="", *, bold=False, size=11, align="left", italic=False, color=None):
        p = document.add_paragraph()
        p.alignment = align_map.get(align, WD_ALIGN_PARAGRAPH.LEFT)
        run = p.add_run(text)
        run.bold, run.italic = bold, italic
        run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor(*color)
        return p

    if ctx["draft"]:
        para("ЧЕРНОВИК — проверить перед подписанием", bold=True, size=9, align="right", color=(180, 0, 0))
    if ctx["header"]:
        for i, line in enumerate(ctx["header"]["lines"]):
            para(line, bold=True, size=10.5 if i == 0 else 9.5, align=ctx["header"]["align"])
    if ctx["title"]:
        para(ctx["title"], bold=True, size=14, align="center")
    if ctx["subtitle"]:
        para(ctx["subtitle"], bold=True, size=11.5, align="center")

    def table(rows: list[list[str]], *, header: list[str] | None = None, borders=True, widths=None, size=9.5):
        cols = len(header or rows[0])
        t = document.add_table(rows=0, cols=cols)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        if borders:
            t.style = "Table Grid"
        for r_index, values in enumerate(([header] if header else []) + rows):
            cells = t.add_row().cells
            for c_index, value in enumerate(values):
                cells[c_index].text = ""
                run = cells[c_index].paragraphs[0].add_run(value)
                run.font.size = Pt(size)
                run.bold = bool(header) and r_index == 0
        if widths:
            total = sum(widths)
            for row in t.rows:
                for c_index, cell in enumerate(row.cells):
                    cell.width = Cm(17.5 * widths[c_index] / total)
        return t

    for block in ctx["blocks"]:
        kind = block["type"]
        if kind == "heading":
            level = block.get("level", 2)
            para(block["text"], bold=True, size=13 if level == 1 else 11.5 if level == 2 else 11)
        elif kind == "paragraph":
            para(block["text"], bold=block.get("bold", False), italic=block.get("italic", False),
                 size=block.get("size", 11), align=block.get("align", "left"))
        elif kind == "fields":
            for row in block["rows"]:
                t = table([[row["label"], row["value"]]], borders=False, widths=[38, 62], size=11)
                cell = t.rows[0].cells[1]
                _underline_cell(cell)
                if row["note"]:
                    p = cell.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = p.add_run(row["note"])
                    run.font.size = Pt(7)
                    run.font.color.rgb = RGBColor(70, 70, 70)
        elif kind == "table":
            table(block["rows"] or [[""] * len(block["columns"])], header=block["columns"],
                  widths=block.get("widths"), size=block.get("size", 9.5))
            document.add_paragraph()
        elif kind == "list":
            for item in block["items"]:
                document.add_paragraph(item, style="List Number" if block.get("ordered") else "List Bullet")
        elif kind == "signatures":
            document.add_paragraph()
            for row in block["rows"]:
                t = table([[row["role"], "", row["name"]]], borders=False, widths=[40, 25, 35], size=11)
                _underline_cell(t.rows[0].cells[1])
                document.add_paragraph()
        elif kind == "pagebreak":
            document.add_page_break()
        elif kind == "spacer":
            document.add_paragraph()

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _underline_cell(cell) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:color"), "000000")
    borders.append(bottom)
    tc_pr.append(borders)


# ---------------------------------------------------------------------------
# Excel / CSV / Markdown
# ---------------------------------------------------------------------------
def render_xlsx(sheets) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    if not isinstance(sheets, list) or not sheets:
        raise DocumentError("Для Excel нужен список sheets: [{name, columns, rows}]")
    workbook = Workbook()
    workbook.remove(workbook.active)
    for i, sheet in enumerate(sheets):
        name = re.sub(r"[\[\]:*?/\\]", "_", str(sheet.get("name") or f"Лист{i + 1}"))[:31]
        ws = workbook.create_sheet(name)
        columns = [str(c) for c in sheet.get("columns") or []]
        if columns:
            ws.append(columns)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.freeze_panes = "A2"
        for row in sheet.get("rows") or []:
            ws.append([_cell(v) for v in (row if isinstance(row, list) else [row])])
        for c_index in range(1, ws.max_column + 1):
            letter = get_column_letter(c_index)
            width = max((len(str(cell.value)) for cell in ws[letter] if cell.value is not None), default=8)
            ws.column_dimensions[letter].width = min(max(width + 2, 8), 60)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _cell(value):
    if isinstance(value, (int, float)) or value is None:
        return value
    text = str(value)
    if text.startswith("="):
        return text  # формула — Excel посчитает сам
    try:
        if re.fullmatch(r"-?\d+([.,]\d+)?", text.strip()):
            return float(text.replace(",", ".")) if re.search(r"[.,]", text) else int(text)
    except ValueError:
        pass
    return text


def render_csv(sheets) -> bytes:
    sheet = (sheets or [{}])[0]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    if sheet.get("columns"):
        writer.writerow(sheet["columns"])
    for row in sheet.get("rows") or []:
        writer.writerow(row if isinstance(row, list) else [row])
    return buffer.getvalue().encode("utf-8-sig")
