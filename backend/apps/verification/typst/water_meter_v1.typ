// Протокол поверки счётчика воды.
// Воспроизводит вид документа, который фирма выпускала из .xlsm, — вёрстка
// повторена намеренно, менять её нельзя без согласования с метрологом.
//
// Данные подаются файлом data.json рядом с шаблоном:
//     typst compile --root . water_meter_v1.typ protocol.pdf

#let d = json("data.json")

#set page(
  paper: "a4",
  margin: (top: 1.6cm, bottom: 1.8cm, left: 1.8cm, right: 1.6cm),
  footer: context [
    #set text(size: 9pt)
    #h(1fr) Стр. #counter(page).display() из #counter(page).final().first()
  ],
)
#set text(font: "Liberation Serif", size: 11pt, lang: "ru", hyphenate: false)
#set par(leading: 0.62em, spacing: 0.62em)

#let centered(body, size: 11pt, weight: "regular") = {
  align(center, text(size: size, weight: weight, body))
}

// ─── шапка организации ────────────────────────────────────────────────────
#centered(weight: "bold", size: 11pt)[#d.org.legal_form]
#v(0.25em)
#centered(weight: "bold", size: 12pt)[#d.org.name]
#v(0.25em)
#centered(weight: "bold", size: 9.5pt)[#d.org.address_legal]
#v(0.1em)
#centered(weight: "bold", size: 9.5pt)[#d.org.address_actual]

#v(0.9em)
#centered(weight: "bold", size: 16pt)[ПРОТОКОЛ №#d.number]
#v(0.9em)

#centered(size: 10.5pt)[#d.subject]
#v(0.8em)

Регистрационный номер средства измерений в ФИФ ОЕИ: #d.registry_number \
Принадлежащего: #d.owner \
Место поверки: #d.place

#v(1.8em)
#centered[ОСНОВНЫЕ МЕТРОЛОГИЧЕСКИЕ ХАРАКТЕРИСТИКИ ПОВЕРЯЕМОГО СИ]
#v(1.0em)

Диапазон измерения объемного расхода жидкости:
#v(0.5em)
#for line in d.ranges [
  #line \
]
#v(3.2em)

// ─── условия поверки ──────────────────────────────────────────────────────
// Четвёртая колонка — примечания справа от таблицы, без рамки.
#table(
  columns: (7.2cm, 2.1cm, 2.1cm, auto),
  stroke: (x, y) => if x < 3 { 0.5pt } else { none },
  align: (x, y) => if x == 0 and y > 0 { left + horizon } else { center + horizon },
  inset: (x: 5pt, y: 6pt),

  table.cell(align: center + horizon)[условия поверки],
  table.cell[в начале\ поверки],
  table.cell[в конце\ поверки],
  table.cell[],

  ..d.conditions.map(row => (
    [#row.label],
    [#row.start],
    [#row.end],
    table.cell(align: left + horizon, text(size: 8.5pt)[#row.note]),
  )).flatten()
)

#v(1.8em)
#centered[ЭТАЛОНЫ, ПРИМЕНЯЕМЫЕ ПРИ ПОВЕРКЕ]
#v(0.6em)
#for standard in d.standards [
  #standard \
]

#v(1.4em)
#centered[МЕТОДИКА ПОВЕРКИ]
#v(0.6em)
#d.method

#v(1.4em)
#centered[РЕЗУЛЬТАТЫ ПОВЕРКИ]
#v(1.0em)

#for check in d.checks [
  #check \
]

#pagebreak()
#v(1.2em)

// ─── таблица измерений ────────────────────────────────────────────────────
#let n = d.rows.len()

#table(
  columns: (4.3cm, 1.45cm, 1.55cm, 1.85cm, 1.85cm, 1.5cm, 1.4cm, 1.0cm, 1.25cm),
  stroke: 0.5pt,
  align: center + horizon,
  // Строки данных выше шапки — как в прежнем документе.
  inset: (x, y) => if y > 2 { (x: 3pt, y: 15pt) } else { (x: 3pt, y: 4pt) },
  table.cell(rowspan: 3, align: center + horizon)[Режим измерений],
  table.cell(colspan: 8)[Результаты измерений],

  table.cell(rowspan: 2, text(size: 9.5pt)[Класс\ счетчика]),
  table.cell(rowspan: 2)[Расход, м#super[3]/ч],
  table.cell(colspan: 2)[V воды счёт., м#super[3]],
  table.cell(rowspan: 2, text(size: 9pt)[Vсчет\ (Vij =\ K\*N ij), м#super[3]]),
  table.cell(rowspan: 2)[Vэтал, м#super[3]],
  table.cell(rowspan: 2)[δ, %],
  table.cell(rowspan: 2, text(size: 10pt)[δдоп, %]),

  table.cell(text(size: 9pt)[начало\ измерений]),
  table.cell(text(size: 9pt)[конец\ измерений]),

  ..d.rows.enumerate().map(pair => {
    let i = pair.at(0)
    let row = pair.at(1)
    let cells = (table.cell(align: center + horizon, text(size: 9pt)[#row.mode_label]),)
    // Класс счётчика — одна ячейка на всю таблицу, как в оригинале.
    if i == 0 { cells.push(table.cell(rowspan: n)[#d.meter_class]) }
    cells + (
      [#row.flow_rate],
      [#row.reading_start],
      [#row.reading_end],
      [#row.volume_meter],
      [#row.volume_standard],
      [#row.error_pct],
      [± #row.limit_pct],
    )
  }).flatten()
)

#v(0.4em)
#grid(
  columns: (2.4cm, 1fr),
  text(size: 9.5pt)[K= #d.pulse_weight],
  text(size: 9pt)[, где K - коэффициент преобразования счетчика, значение которого
  указывается на счетчике конкретного типа или в его эксплуатационных документах, м3/имп],
)

#v(1.6em)
#centered[ЗАКЛЮЧЕНИЕ]
#v(1.0em)

#d.conclusion

#v(1.8em)
#grid(
  columns: (2.9cm, 3.4cm, 3.6cm, 3.4cm, 1fr),
  align: (left + bottom, center + bottom, left + bottom, left + bottom, left + bottom),
  [Поверитель],
  [#box(width: 100%, stroke: (bottom: 0.5pt), height: 1.1em)],
  [#h(0.3cm) #d.verifier],
  [Дата поверки],
  [#h(0.6cm) #d.date],
)
#grid(
  columns: (2.9cm, 3.4cm, 1fr),
  [], align(center, text(size: 8pt)[(подпись)]), [],
)
