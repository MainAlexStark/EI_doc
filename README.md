# EI_doc

Документооборот поверочных работ ООО «Единица Измерения».
Заменяет десктопное приложение EI_protocols (PyQt6 + Excel COM) и поглощает EI_HUB.

Документация: [архитектура](docs/architecture.md) · [нумерация протоколов](docs/numbering.md) ·
[условия поверки](docs/conditions.md) · [измерения](docs/measurements.md) ·
[журнал и нормоконтроль](docs/journal.md).

## Состояние

Этап 0 — фундамент. Готово:

- Django 5 + DRF + SimpleJWT, восемь приложений (четыре с кодом, четыре — заглушки под следующие этапы)
- Пользователи, сотрудники, аттестации, полевые устройства
- Справочники: семейства СИ, типы из Госреестра, эталоны, методики, версионируемые шаблоны протоколов
- Поверки, протоколы и **сервис нумерации**: номера по времени поверки, сброс 1 января,
  дробные подномера для вставок задним числом (00772/1)
- **Журнал погоды** — поведение старого приложения сохранено: условия на дату берутся
  из журнала, иначе генерируются в диапазоне и запоминаются
- **Подсказка типов СИ из реестра ФИФ** — поиск по номеру или названию с обязательным
  показом изготовителя
- **Калькулятор счётчиков воды** с золотыми тестами на 33 строках из пяти выпущенных
  протоколов — числа совпадают с Excel до последнего знака
- **Ввод измерений** с экрана и со скана бланка: объём показаниями, импульсами или
  напрямую, вердикт выводится из чисел, спорные распознанные строки уходят на сверку
- **Печать протокола** Typst-шаблоном, вид один в один с прежним документом из `.xlsm`
- **Веб-журнал**: таблица с фильтрами и поиском, состояние протокола, выгрузка в `.xlsx`
  формата ФИФ ОЕИ — те же 49 колонок, что в прежнем журнале
- **Экран нормоконтроля**: предпросмотр пересчёта номеров до применения,
  подпись пачкой, самопроверка хронологии
- **Справочник 60 типов счётчиков** с метрологическими характеристиками,
  вынутый из рабочих шаблонов: `python manage.py seed_catalog`
- Аудит изменений по ключевым моделям (`django-simple-history`)
- Импорт исторического журнала: на реальном журнале переносится 8073 строки из 8126
- docker compose: gunicorn, Celery worker + beat, PostgreSQL, Redis — за общим Caddy (см. «Развёртывание на VPS»)
- Фронтенд журнала встроен в тот же образ (собирается на этапе `docker build`,
  отдаётся Django через whitenoise) — адрес после деплоя один: `https://<домен>/`
- 110 тестов

Этап 1 закрыт. Дальше этап 2: EI_Hub внутрь, роутинг заявок, наряды, задачи.

## Запуск

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # положить в SECRET_KEY
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Наружу ничего не выставлено (см. «Развёртывание на VPS» — публично торчит
только Caddy); для локальной проверки раскомментируйте `ports` у `web` в
`docker-compose.yml`. Тогда админка — `http://localhost:8000/admin/`,
схема API — `/api/docs/`.

## Развёртывание на VPS

Тем же способом, что и MCP-серверы (`ozon-seller-mcp`, `k8s-mcp`): общий Caddy
на машину, один Caddyfile на все сервисы, TLS от Let's Encrypt сам.

```bash
git clone https://github.com/MainAlexStark/EI_doc.git
cd EI_doc
./deploy.sh eidoc.example.com
docker compose exec web python manage.py createsuperuser
```

Скрипт заведёт `.env` из примера, сгенерирует `SECRET_KEY` и пароль БД,
пропишет домен в `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`, поднимет (или
переиспользует) общий Caddy в `/opt/infrastructure`, соберёт и запустит
сервер и дождётся ответа `/healthz` по HTTPS.

```bash
./deploy.sh update      # обновить до свежего коммита, с откатом при неудаче
./deploy.sh status      # что запущено и отвечает ли /healthz
./deploy.sh logs
./deploy.sh secrets     # где лежат пароли и адрес
```

Секреты живут в `.env` в корне репозитория (права 600) — тот же файл, что
читает локальная разработка без Docker, повторный запуск `./deploy.sh` их не
перевыпускает.

## Разработка без Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt

cd backend
DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py seed_catalog
DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py seed_demo
DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py runserver
```

Фронт отдельным процессом:

```bash
cd frontend && npm install && npm run dev    # http://localhost:5173
```

Вход демо-данных: `metrolog@ei.test` / `demo12345`. `seed_demo` работает
только при `DEBUG=True`.

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

Маппинг под «Журнал учёта поверочных работ Стариков» уже готов:

```bash
python manage.py import_journal \
    --file "Журнал учёта поверочных работ Стариков.xlsx" \
    --mapping apps/verification/mappings/journal_starikov.json --dry-run
```

На журнале от 03.09.2026: прочитано 8126 строк, перенеслось 8073, отклонено 53 —
все из-за пустой графы «Модификация СИ», то есть у записи нет наименования СИ.

Сопоставление идёт по **первой строке заголовка**, а не по индексу столбца:
заголовки в журнале — многострочные описания полей формата ФИФ, и перестановка
колонок ничего не ломает. Для другого журнала: `--inspect` напечатает заголовки
и заготовку маппинга.

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
