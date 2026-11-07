// Маршрутный лист командировки — первая часть PDF-пакета на выезд
// (apps.logistics.service.render_pack). За ним идут бланки на режимные
// объекты. Данные — data.json рядом с шаблоном.

#let d = json("data.json")

#set page(paper: "a4", margin: (top: 14mm, bottom: 14mm, left: 14mm, right: 14mm),
  footer: context align(right, text(size: 8pt, fill: gray)[
    #d.title · сформирован #d.generated · стр. #counter(page).display()
  ]))
#set text(font: "Liberation Serif", size: 10.5pt, lang: "ru", hyphenate: false)
#set par(leading: 0.55em, spacing: 0.6em)

#align(center, text(size: 15pt, weight: "bold")[Маршрутный лист])
#align(center, text(size: 12pt)[#d.title, #d.dates])
#v(4mm)

#grid(columns: (auto, 1fr), column-gutter: 6pt, row-gutter: 4pt,
  [*Поверители:*], [#d.employees.join(", ")],
  ..if d.vehicle != "" { ([*Транспорт:*], [#d.vehicle]) } else { () },
  ..if d.note != "" { ([*Примечание:*], [#d.note]) } else { () },
)

#if d.standards.len() > 0 [
  #v(3mm)
  *Эталоны с собой*
  #table(columns: (1fr, auto, auto, auto), inset: 4pt, stroke: 0.4pt,
    table.header([*Наименование*], [*Рег. номер*], [*Зав. №*], [*Годен до*]),
    ..d.standards.map(s => (s.name, s.number, s.serial, s.valid_until)).flatten()
  )
]

#for day in d.days [
  #v(4mm)
  #text(size: 12pt, weight: "bold")[#day.day]
  #table(columns: (auto, auto, 1fr, auto, auto), inset: 4pt, stroke: 0.4pt,
    table.header([*№*], [*Прибытие*], [*Адрес, клиент, допуск*], [*Наряд*], [*Отметка*]),
    ..day.stops.map(s => (
      [#s.position],
      [#s.eta #if s.window != "" [\ #text(size: 8.5pt)[окно #s.window]]],
      [#s.address
        #if s.client != "" [\ #text(size: 9pt)[#s.client]]
        #if s.contact != "" [\ #text(size: 9pt)[Контакт: #s.contact]]
        #if s.access != "" [\ #text(size: 9pt)[Допуск: #s.access]]
        #if s.restricted [\ #text(size: 9pt, weight: "bold")[Режимный объект — бумажный бланк]]],
      [#s.work_order],
      [],
    )).flatten()
  )
]
