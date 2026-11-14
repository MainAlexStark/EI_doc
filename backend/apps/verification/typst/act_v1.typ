// Акт приёма средств измерений в поверку / акт выдачи после поверки.
// Данные — data.json рядом (apps.verification.api_orders.act_context):
//     typst compile --root . act_v1.typ act.pdf

#let d = json("data.json")

#set page(paper: "a4", margin: (top: 16mm, bottom: 16mm, left: 18mm, right: 14mm))
#set text(font: "Liberation Serif", size: 11pt, lang: "ru", hyphenate: false)
#set par(leading: 0.55em, spacing: 0.7em)

#let field(label, value) = if value != "" and value != none [
  #text(weight: "bold")[#label:] #value \
]

#align(center)[
  #text(size: 13pt, weight: "bold")[#d.title] \
  #text(size: 12pt)[№ #d.number от #d.date]
]
#v(0.8em)

#grid(
  columns: (1fr, 1fr),
  column-gutter: 10mm,
  [
    #text(weight: "bold")[Исполнитель] \
    #d.org.name \
    #text(size: 9.5pt)[#d.org.address]
  ],
  [
    #text(weight: "bold")[Заказчик] \
    #d.customer.name \
    #text(size: 9.5pt)[
      #if d.customer.inn != "" [ИНН #d.customer.inn]
      #if d.customer.kpp != "" [, КПП #d.customer.kpp]
      #if d.customer.address != "" [\ #d.customer.address]
      #if d.customer.phone != "" [\ тел. #d.customer.phone]
    ]
  ],
)
#v(0.4em)
#field("Основание", d.basis)
#field(if d.kind == "receive" { "Сдал" } else { "Получил" }, d.contact)

#v(0.4em)
#if d.kind == "receive" [
  Исполнитель принял для проведения поверки следующие средства измерений:
] else [
  Исполнитель передал Заказчику после проведения поверки следующие средства измерений:
]
#v(0.2em)

#let head(body) = text(size: 9.5pt, weight: "bold", body)
#let cell(body) = text(size: 9.5pt, body)

#table(
  columns: if d.kind == "receive" { (8mm, 1fr, 26mm, 34mm, 16mm) } else { (8mm, 1fr, 22mm, 28mm, 18mm, 32mm) },
  stroke: 0.5pt,
  inset: (x: 4pt, y: 4pt),
  align: (center + horizon, left + horizon, center + horizon, center + horizon, center + horizon, center + horizon),
  table.header(
    head[№], head[Наименование, тип СИ], head[Рег. № ФИФ], head[Заводской номер],
    ..if d.kind == "receive" { (head[Год вып.],) } else { (head[Результат], head[Протокол]) },
  ),
  ..d.rows.map(r => (
    cell(str(r.n)), cell(r.name), cell(r.registry), cell(r.serial),
    ..if d.kind == "receive" { (cell(str(r.year)),) } else { (cell(r.result), cell(r.protocol)) },
  )).flatten(),
)

#v(0.3em)
Всего: #d.total шт.
#if d.kind == "issue" [ Пригодны: #d.suitable шт. #if d.unsuitable > 0 [Непригодны: #d.unsuitable шт.]]
#if d.note != "" [\ #text(size: 10pt)[Примечание: #d.note]]

#v(0.6em)
#if d.kind == "receive" [
  Средства измерений приняты в количестве #d.total шт. Внешний вид и комплектность проверены при приёме в присутствии представителя Заказчика.
] else [
  Средства измерений переданы в количестве #d.total шт. Сведения о результатах поверки переданы в ФИФ ОЕИ. Претензий к комплектности Заказчик не имеет.
]

#v(2.2em)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 14mm,
  row-gutter: 2.4em,
  [
    #text(weight: "bold")[#if d.kind == "receive" [Принял] else [Передал]] (Исполнитель) \
    #v(1.6em)
    #box(width: 40mm, stroke: (bottom: 0.5pt)) / #d.employee
  ],
  [
    #text(weight: "bold")[#if d.kind == "receive" [Сдал] else [Получил]] (Заказчик) \
    #v(1.6em)
    #box(width: 40mm, stroke: (bottom: 0.5pt)) / #if d.contact != "" [#d.contact] else [#box(width: 30mm, stroke: (bottom: 0.5pt))]
  ],
)
