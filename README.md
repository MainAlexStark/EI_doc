# EI_doc

Документооборот поверочных работ ООО «Единица Измерения».
Заменяет десктопное приложение EI_protocols (PyQt6 + Excel COM) и поглощает EI_HUB.

Архитектура целиком — `docs/architecture.md`. Нумерация протоколов — `docs/numbering.md`.

## Состояние

Этап 0 — фундамент. Готово:

- Django 5 + DRF + SimpleJWT, восемь приложений (три с моделями, пять — заглушки под следующие этапы)
- Пользователи, сотрудники, аттестации, полевые устройства
- Справочники: семейства СИ, типы из Госреестра, эталоны, методики, версионируемые шаблоны протоколов
- Поверки, протоколы и **сервис нумерации** — 19 тестов
- Аудит изменений по ключевым моделям (`django-simple-history`)
- Импорт исторического журнала из `.xlsx` по заголовкам столбцов
- docker compose: nginx, gunicorn, Celery worker + beat, PostgreSQL, Redis

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

## Что нельзя делать в этом репозитории

- Класть `.env` в git. Секреты живут на сервере.
- Ставить на сервер Excel, LibreOffice или headless-браузер ради генерации документов.
- Присваивать номер протокола руками в обход `apps.verification.numbering`.
- Править подписанный протокол. Правка — это новая версия со ссылкой на предыдущую.
