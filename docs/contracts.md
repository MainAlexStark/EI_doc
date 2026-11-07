# Договоры

Код: `backend/apps/hub/contracts.py`, кнопка «Договор» в заявке (вкладка
«Заявки»). Выпускает менеджер, руководитель или владелец (`core.manage_contracts`).

- Договор — `.docx` по шаблону **docxtpl**. Без загруженного шаблона берётся
  встроенный `apps/hub/templates_docx/contract_default.docx` (пересобирается
  `python tools/build_contract_template.py`).
- Свой шаблон: открыть встроенный в Word, поправить текст, не трогая поля
  в `{{ }}` / `{% %}`, загрузить в админке «Шаблоны договоров». Можно
  отдельный для физлиц и юрлиц.
- Номер сквозной за год: `Д-2026-0001`, выдаётся под блокировкой. Выпущенный
  файл не перезаписывается, хэш хранится. Правка — новый договор.
- Клиент: из наряда — его клиент; из заявки — найденный по телефону или новый.
- Реквизиты организации — `ORG_*` в `.env`; незаполненные печатаются чертой.

## Поля шаблона

| Поле | Что это |
|---|---|
| `number`, `date` | номер и дата договора |
| `org.name`, `org.full_name`, `org.inn`, `org.kpp`, `org.ogrn`, `org.address`, `org.bank`, `org.bik`, `org.account`, `org.corr_account`, `org.director`, `org.accreditation`, `org.phone`, `org.email` | реквизиты исполнителя |
| `client.name`, `client.short_name`, `client.inn`, `client.phone`, `client.email`, `client.is_org` | заказчик |
| `address`, `date_planned` | адрес объекта и плановая дата |
| `items` (`name`, `quantity`, `price`, `subtotal`) | строки таблицы: `{%tr for item in items %}` |
| `total`, `total_words` | сумма цифрами и прописью |
| `privacy_url` | ссылка на политику ПДн |
