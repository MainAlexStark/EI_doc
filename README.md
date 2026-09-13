# EI_doc

Документооборот поверочных работ ООО «Единица Измерения».
Заменяет десктопное приложение EI_protocols (PyQt6 + Excel COM) и поглощает EI_HUB.

Документация: [архитектура](docs/architecture.md) · [нумерация протоколов](docs/numbering.md) ·
[условия поверки](docs/conditions.md).

## Состояние

Этап 0 — фундамент. Готово:

- Django 5 + DRF + SimpleJWT, восемь приложений (четыре с кодом, четыре — заглушки под следующие этапы)
- Пользователи, сотрудники, аттестации, полевые устройства
- Справочники: семейства СИ, типы из Госреестра, эталоны, методики, версионируемые шаблоны протоколов
- Поверки, протоколы и **сервис нумерации**: номера по времени поверки, сброс 1 января,
  литерные подномера для вставок задним числом
- **Журнал погоды** — поведение старого приложения сохранено: условия на дату берутся
  из журнала, иначе генерируются в диапазоне и запоминаются
- **Подсказка типов СИ из реестра ФИФ** — поиск по номеру или названию с обязательным
  показом изготовителя
- Аудит изменений по ключевым моделям (`django-simple-history`)
- Импорт исторического журнала из `.xlsx` по заголовкам столбцов
- docker compose: nginx, gunicorn, Celery worker + beat, PostgreSQL, Redis
- 44 теста

Дальше — этап 1: калькуляторы, золотые тесты на выпущенных протоколах, Typst-шаблон, веб-журнал.

## Запуск

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # положить в SECRET_KEY
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Админка — `http://localhost/admin/`, схема API — `/api/docs/`.

## Разработка без Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
python manage.py migrate
python manage.py runserver
```

Тесты (SQLite, быстро):

```bash
cd backend && pytest
```

Перед релизом тесты гоняются на PostgreSQL — SQLite не проверяет `select_for_update`
и частичные уникальные индексы, а нумерация держится именно на них:

```bash
cd backend && DJANGO_SETTINGS_MODULE=config.settings.dev pytest -p no:cacheprovider
```

## Импорт старого журнала

```bash
python manage.py import_journal --file journal.xlsx --inspect
# заполнить mapping.json заголовками, которые напечатала команда
python manage.py import_journal --file journal.xlsx --mapping mapping.json --dry-run
python manage.py import_journal --file journal.xlsx --mapping mapping.json
```

Маппинг идёт по заголовкам, а не по индексам столбцов, — перестановка колонок
в исходном файле ничего не ломает.

## Реестр ФИФ

Схема открытого API Аршина публично не документирована и из среды разработки
недоступна. Перед первым использованием подсказки типов СИ выполните **на сервере**:

```bash
python manage.py fif_probe --query СВК
```

Команда покажет фактические имена полей в ответе и скажет, каких соответствий не
хватает, — после чего `FIELD_ALIASES` в `apps/arshin/fif.py` уточняется одной правкой.

## Что нельзя делать в этом репозитории

- Класть `.env` в git. Секреты живут на сервере.
- Ставить на сервер Excel, LibreOffice или headless-браузер ради генерации документов.
- Присваивать номер протокола руками в обход `apps.verification.numbering`.
- Править подписанный протокол. Правка — это новая версия со ссылкой на предыдущую.
