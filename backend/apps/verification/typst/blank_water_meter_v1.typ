// Печатный бланк поверки счётчика воды — для объектов без связи (второй
// срез офлайна, режимные объекты — Site.is_restricted). Один бланк — один
// счётчик; какой именно счётчик попадёт на бланк, заранее не известно,
// поэтому здесь нет ни типа СИ, ни зав. номера — их поверитель вписывает
// от руки на месте, а на компьютере распознаёт и сверяет система
// (apps.verification.scan, apps.hub.yandex_vision).
//
// Реперные метки по углам страницы (background) и QR наряда — то, по чему
// фото выравнивается и опознаётся на сервере. Их геометрия завязана на
// apps.verification.scan.CANVAS_*/MARKER_MARGIN_PX — менять размеры и
// отступы можно только вместе.
//
// Данные подаются файлом data.json, QR — уже готовым PNG (qr.png), оба
// рядом с шаблоном:
//     typst compile --root . blank_water_meter_v1.typ blank.pdf

#let d = json("data.json")

#let marker(size: 12mm) = rect(width: size, height: size, fill: black)

#set page(
  paper: "a4",
  margin: (top: 20mm, bottom: 12mm, left: 16mm, right: 16mm),
  background: {
    place(top + left, dx: 5mm, dy: 5mm, marker())
    place(top + right, dx: -17mm, dy: 5mm, marker())
    place(bottom + left, dx: 5mm, dy: -17mm, marker())
    place(bottom + right, dx: -17mm, dy: -17mm, marker())
  },
)
#set text(font: "Liberation Serif", size: 10.5pt, lang: "ru", hyphenate: false)
#set par(leading: 0.6em, spacing: 0.6em)

#let centered(body, size: 10.5pt, weight: "regular") = {
  align(center, text(size: size, weight: weight, body))
}

#let blank_field(label, width: 1fr) = grid(
  columns: (auto, width),
  column-gutter: 4pt,
  align: (left + bottom, left + bottom),
  [#label:], box(width: 100%, stroke: (bottom: 0.6pt), height: 1.3em),
)

#let checkbox() = box(width: 3.6mm, height: 3.6mm, stroke: 0.6pt)

#let page_body() = {
  grid(
    columns: (1fr, 28mm),
    align: (left, right),
    [
      #text(weight: "bold", size: 12pt)[#d.org.name] \
      #text(size: 9.5pt)[Бланк поверки счётчика воды]
    ],
    image("qr.png", width: 26mm),
  )
  v(0.6em)
  line(length: 100%, stroke: 0.4pt)
  v(0.8em)

  text(weight: "bold")[Наряд №#d.work_order_number] от #d.date \
  Клиент: #d.client \
  Объект: #d.site

  v(1.0em)
  centered(weight: "bold")[ДАННЫЕ СЧЁТЧИКА — заполняется от руки]
  v(0.6em)
  blank_field("Тип / модель счётчика")
  v(0.5em)
  grid(
    columns: (1fr, 3.6cm, 3.6cm),
    column-gutter: 10pt,
    blank_field("Заводской номер", width: 100%),
    blank_field("Год выпуска", width: 100%),
    blank_field("Коэфф. K, м³/имп (если есть)", width: 100%),
  )
  v(0.5em)
  grid(
    columns: (1fr, 1fr),
    column-gutter: 10pt,
    [#checkbox() г/в #h(10pt) #checkbox() х/в],
    [Класс счётчика: #checkbox() А #h(10pt) #checkbox() В],
  )
  v(0.3em)
  blank_field("Температура поверочной жидкости, °С", width: 3.6cm)

  v(1.0em)
  centered(weight: "bold")[РЕЗУЛЬТАТЫ ПОВЕРКИ]
  v(0.6em)
  for check in d.checks [
    #checkbox() #check \
    #v(0.2em)
  ]

  v(0.8em)
  table(
    columns: (3.4cm, 1.6cm, 3.4cm, 3.4cm, 3.4cm),
    stroke: 0.5pt,
    align: center + horizon,
    inset: (x: 4pt, y: 10pt),
    table.cell[Режим измерений], table.cell[t, с],
    table.cell[Показания в начале, м³], table.cell[Показания в конце, м³],
    table.cell[Vэтал (по установке), м³],
    ..d.rows.map(row => (
      [#row.label], [#row.seconds], [], [], [],
    )).flatten()
  )
  v(0.3em)
  text(size: 8.5pt)[Если у счётчика импульсный выход, показания можно заменить числом импульсов —
  впишите его в графу «Показания в конце» и обведите.]

  v(1.0em)
  centered(weight: "bold")[НЕПРИГОДЕН]
  v(0.4em)
  [#checkbox() Счётчик признан непригодным (не по погрешности — осмотр, повреждение и т. п.)]
  v(0.3em)
  text(size: 9pt)[Частые причины: #d.common_unsuitability_reasons.join("; ") — можно обвести подходящую или вписать свою.]
  v(0.3em)
  blank_field("Причина")

  v(1.2em)
  grid(
    columns: (2.6cm, 3.2cm, 2.6cm, 3.2cm),
    align: (left + bottom, center + bottom, left + bottom, left + bottom),
    [Поверитель], box(width: 100%, stroke: (bottom: 0.5pt), height: 1.1em),
    [Дата], box(width: 100%, stroke: (bottom: 0.5pt), height: 1.1em),
  )
}

#for i in range(d.copies) {
  page_body()
  if i < d.copies - 1 { pagebreak() }
}
