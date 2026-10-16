// Универсальный документ ассистента: протоколы (аттестации ИО, поверки по
// образцу), справки, акты. Содержимое — блоки из data.json (см.
// apps/assistant/documents.py), строки выводятся как текст, не как разметка.

#let d = json("data.json")

#set page(
  paper: "a4",
  margin: (top: 1.5cm, bottom: 1.8cm, left: 2cm, right: 1.5cm),
  background: if d.at("draft", default: false) {
    rotate(-40deg, text(size: 90pt, fill: rgb(200, 0, 0, 28), weight: "bold")[ЧЕРНОВИК])
  },
  footer: context [
    #set text(size: 8.5pt, fill: rgb(90, 90, 90))
    #d.at("footer", default: "") #h(1fr) стр. #counter(page).display() из #counter(page).final().first()
  ],
)
#set text(font: ("Liberation Serif", "Times New Roman", "Libertinus Serif"), size: 11pt, lang: "ru", hyphenate: false)
#set par(leading: 0.55em, spacing: 0.7em, justify: false)

#let al(name) = if name == "center" { center } else if name == "right" { right } else { left }

// ─── шапка ──────────────────────────────────────────────────────────────
#let header = d.at("header", default: none)
#if header != none and header.at("lines", default: ()).len() > 0 {
  align(al(header.at("align", default: "center")))[
    #for (i, line) in header.lines.enumerate() {
      text(size: if i == 0 { 10.5pt } else { 9.5pt }, weight: "bold", line)
      linebreak()
    }
  ]
  v(0.6em)
}

#if d.at("title", default: "") != "" {
  align(center, text(size: 14pt, weight: "bold", d.title))
}
#if d.at("subtitle", default: "") != "" {
  align(center, text(size: 11.5pt, weight: "bold", d.subtitle))
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

#for block in d.blocks {
  let kind = block.at("type", default: "paragraph")
  if kind == "heading" {
    let level = block.at("level", default: 2)
    v(0.4em)
    text(size: if level == 1 { 13pt } else if level == 2 { 11.5pt } else { 11pt }, weight: "bold", block.text)
    v(0.15em)
  } else if kind == "paragraph" {
    align(al(block.at("align", default: "left")))[
      #text(
        size: block.at("size", default: 11) * 1pt,
        weight: if block.at("bold", default: false) { "bold" } else { "regular" },
        style: if block.at("italic", default: false) { "italic" } else { "normal" },
        block.text,
      )
    ]
  } else if kind == "fields" {
    for row in block.rows { field-row(row) }
  } else if kind == "list" {
    let items = block.items.map(it => text(it))
    if block.at("ordered", default: false) { enum(..items) } else { list(..items) }
  } else if kind == "table" {
    let cols = block.columns.len()
    let widths = block.at("widths", default: none)
    set text(size: block.at("size", default: 9.5) * 1pt)
    table(
      columns: if widths != none { widths.map(w => w * 1fr) } else { cols * (1fr,) },
      inset: 4pt,
      align: (x, y) => if y == 0 { center + horizon } else { left + horizon },
      table.header(..block.columns.map(c => text(weight: "bold", c))),
      ..block.rows.flatten().map(c => text(str(c))),
    )
  } else if kind == "signatures" {
    v(1em)
    for row in block.rows {
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
    v(block.at("size", default: 1) * 1em)
  }
}
