// Универсальный документ ассистента: протоколы (аттестации ИО, поверки по
// образцу), справки, акты. Содержимое — блоки из data.json (см.
// apps/assistant/documents.py), строки выводятся как текст, не как разметка.

#let d = json("data.json")
// "protocol" — протокол поверки в фирменном виде, как у счётчиков воды (verification/typst/water_meter_v1.typ):
// шапка фирмы, «ПРОТОКОЛ №…» 16 pt, разделы прописными по центру, таблицы в тонкую рамку, подпись с датой.
#let protocol = d.at("style", default: "") == "protocol"

#set page(
  paper: "a4",
  margin: if protocol { (top: 1.6cm, bottom: 1.8cm, left: 1.8cm, right: 1.6cm) } else { (top: 1.5cm, bottom: 1.8cm, left: 2cm, right: 1.5cm) },
  background: if d.at("draft", default: false) {
    rotate(-40deg, text(size: 90pt, fill: rgb(200, 0, 0, 28), weight: "bold")[ЧЕРНОВИК])
  },
  footer: context [
    #set text(size: if protocol { 9pt } else { 8.5pt }, fill: if protocol { black } else { rgb(90, 90, 90) })
    #text(size: 8pt, fill: rgb(90, 90, 90), d.at("footer", default: "")) #h(1fr) #if protocol [Стр.] else [стр.] #counter(page).display() из #counter(page).final().first()
  ],
)
#set text(font: ("Liberation Serif", "Times New Roman", "Libertinus Serif"), size: 11pt, lang: "ru", hyphenate: false)
#set par(leading: if protocol { 0.62em } else { 0.55em }, spacing: if protocol { 0.62em } else { 0.7em }, justify: false)

#let al(name) = if name == "center" { center } else if name == "right" { right } else { left }

// ─── шапка ──────────────────────────────────────────────────────────────
#let header = d.at("header", default: none)
#if header != none and header.at("lines", default: ()).len() > 0 {
  if protocol {
    // Как у счётчиков воды: форма, название, два адреса.
    let sizes = (11pt, 12pt)
    for (i, line) in header.lines.enumerate() {
      align(center, text(size: sizes.at(i, default: 9.5pt), weight: "bold", line))
      v(if i < 2 { 0.25em } else { 0.1em })
    }
    v(0.9em)
  } else {
    align(al(header.at("align", default: "center")))[
      #for (i, line) in header.lines.enumerate() {
        text(size: if i == 0 { 10.5pt } else { 9.5pt }, weight: "bold", line)
        linebreak()
      }
    ]
    v(0.6em)
  }
}

#if d.at("title", default: "") != "" {
  align(center, text(size: if protocol { 16pt } else { 14pt }, weight: "bold", d.title))
  if protocol { v(0.9em) }
}
#if d.at("subtitle", default: "") != "" {
  if protocol {
    align(center, text(size: 10.5pt, d.subtitle))
    v(0.8em)
  } else {
    align(center, text(size: 11.5pt, weight: "bold", d.subtitle))
  }
}
#v(0.6em)

#let field-row(row) = {
  grid(
    columns: (38%, 1fr),
    column-gutter: 8pt,
    text(row.at("label", default: "")),
    [
      #box(width: 100%, stroke: (bottom: 0.5pt), inset: (bottom: 2pt), text(row.at("value", default: "")))
      #if row.at("note", default: "") != "" {
        align(center, text(size: 7pt, fill: rgb(70, 70, 70), row.note))
      }
    ],
  )
  v(0.25em)
}

#for b in d.blocks {
  let kind = b.at("type", default: "paragraph")
  if kind == "heading" and protocol {
    v(1.4em)
    align(center, text(size: 11pt, upper(b.text)))
    v(0.6em)
  } else if kind == "heading" {
    let level = b.at("level", default: 2)
    v(0.4em)
    text(size: if level == 1 { 13pt } else if level == 2 { 11.5pt } else { 11pt }, weight: "bold", b.text)
    v(0.15em)
  } else if kind == "paragraph" and protocol and b.at("tight", default: false) {
    // Строки сведений («Регистрационный номер…», параметры, эталоны) — плотно, как в протоколе счётчика.
    block(spacing: 0.62em, text(size: b.at("size", default: 11) * 1pt, b.text))
  } else if kind == "paragraph" {
    align(al(b.at("align", default: "left")))[
      #text(
        size: b.at("size", default: 11) * 1pt,
        weight: if b.at("bold", default: false) { "bold" } else { "regular" },
        style: if b.at("italic", default: false) { "italic" } else { "normal" },
        b.text,
      )
    ]
  } else if kind == "fields" {
    for row in b.rows { field-row(row) }
  } else if kind == "list" {
    let items = b.items.map(it => text(it))
    if b.at("ordered", default: false) { enum(..items) } else { list(..items) }
  } else if kind == "table" and protocol {
    let cols = b.columns.len()
    let widths = b.at("widths", default: none)
    let free = b.at("free_last", default: false)
    set text(size: b.at("size", default: 9.5) * 1pt)
    v(0.4em)
    table(
      columns: if widths != none { widths.map(w => w * 1fr) } else { cols * (1fr,) },
      // free_last — последняя колонка без рамки: нормы справа от таблицы условий.
      stroke: (x, y) => if free and x == cols - 1 { none } else { 0.5pt },
      inset: (x: 5pt, y: 6pt),
      align: (x, y) => if free and x == cols - 1 { left + horizon } else if x == 0 and y > 0 and not b.at("center_all", default: false) { left + horizon } else { center + horizon },
      ..b.columns.map(c => text(c)),
      ..b.rows.enumerate().map(pair => pair.at(1).enumerate().map(cell => {
        if free and cell.at(0) == cols - 1 { text(size: 8.5pt, str(cell.at(1))) } else { text(str(cell.at(1))) }
      })).flatten(),
    )
    v(0.4em)
  } else if kind == "table" {
    let cols = b.columns.len()
    let widths = b.at("widths", default: none)
    set text(size: b.at("size", default: 9.5) * 1pt)
    table(
      columns: if widths != none { widths.map(w => w * 1fr) } else { cols * (1fr,) },
      inset: 4pt,
      align: (x, y) => if y == 0 { center + horizon } else { left + horizon },
      table.header(..b.columns.map(c => text(weight: "bold", c))),
      ..b.rows.flatten().map(c => text(str(c))),
    )
  } else if kind == "signatures" and protocol {
    v(1.6em)
    for row in b.rows {
      let dated = row.at("date", default: "") != ""
      grid(
        columns: if dated { (2.8cm, 4.2cm, 3.6cm, 3.2cm, 1fr) } else { (2.8cm, 4.2cm, 1fr) },
        column-gutter: 8pt,
        row-gutter: 2pt,
        text(row.at("role", default: "")),
        box(width: 100%, stroke: (bottom: 0.5pt), inset: (bottom: 2pt))[],
        text(row.at("name", default: "")),
        ..if dated { (text("Дата поверки"), text(row.date)) } else { () },
        [],
        align(center, text(size: 7pt, "(подпись)")),
        [],
        ..if dated { ([], []) } else { () },
      )
      v(0.9em)
    }
  } else if kind == "signatures" {
    v(1em)
    for row in b.rows {
      grid(
        columns: (40%, 25%, 1fr),
        column-gutter: 8pt,
        text(row.at("role", default: "")),
        box(width: 100%, stroke: (bottom: 0.5pt), inset: (bottom: 2pt))[],
        text(row.at("name", default: "")),
      )
      v(0.9em)
    }
  } else if kind == "pagebreak" {
    pagebreak()
  } else if kind == "spacer" {
    v(b.at("size", default: 1) * 1em)
  }
}
