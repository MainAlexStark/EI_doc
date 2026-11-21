# Пересборка интерфейса — 21.11.2026

Бэкенд (нумерация, расчёты, Typst, ФИФ, ассистент, логистика) сохраняется и усиливается.
Интерфейс пересобран вокруг **режимов работы**: человек сначала выбирает, чем занят, и
дальше идёт по потоку без лишних экранов.

## Восемь режимов

| # | Режим | Ключ (hash) | Для кого | Суть потока |
|---|---|---|---|---|
| 1 | **Город** | `city` | поверители, диспетчер | Выезды по Кирову и Яранску: адреса дня с окнами 2 ч, счётчики у людей, «забрать коробку» с предприятия (забор → приёмка в лаборатории) |
| 2 | **Командировки** | `trips` | поверители, руководство | Поездка на несколько дней: районы, соседние сёла, адреса по дням, карта, офлайн |
| 3 | **Закупки** | `procurements` | менеджер, руководство; поверитель — чтение | Контракт 44/223-ФЗ: документы (ТЗ, контракт, пропуска), агент читает и вытаскивает требования/перечень СИ/сроки, план-факт по заходам |
| 4 | **Приёмки** | `intake` | все | Клиент принёс коробку: заказчик → приборы списком с клавиатуры/сканера → акт приёма → поверка → выдача |
| 5 | **Сайт** | `/` (публичный) | клиенты | Одностраничник фирмы, форма заявки как можно выше |
| 6 | **Заявки** | `requests` | менеджер, руководство | Разбор входящих на потоке: горячие клавиши, назначение одним касанием |
| 7 | **Шаблоны поверки СИ** | `si-templates` | метролог | Поиск типа в ФИФ → методика → чат с ассистентом рядом с шаблоном → «Продолжить» |
| 8 | **Шаблоны аттестации** | `att-templates` | метролог | План аттестации ИО из документов (ГОСТ Р 8.568) → сверка формул на примере → шаблон протокола |

Вспомогательные разделы: Сегодня (главная), Панель (руководство), Журнал, График, Задачи,
Ассистент, Типы СИ, Сотрудники.

## Дизайн-система v2 «Поверочный стол»

Спокойная точность прибора: светлая рабочая поверхность, один уверенный ультрамарин
(родство с чёрно-синим сайтом), сигнальные цвета только для смысла (годен / непригоден / внимание).

| Токен | Значение | Роль |
|---|---|---|
| `--paper` | `#FAFAFC` | фон |
| `--ink` | `#151821` | текст |
| `--graphite` | `#5C6272` | вторичный текст |
| `--rule` | `#E2E5EC` | линии |
| `--ultramarine` | `#2B45E0` | акцент, действие |
| `--signal` | `#E89B00` | внимание, ждёт |
| `--pass` | `#12925A` | годен, выполнено |
| `--fail` | `#D3311F` | непригоден, отказ |

Шрифты (самохостинг через @fontsource, работают офлайн): **Golos Text** — интерфейс
(кириллица в основе), **JetBrains Mono** — только данные: заводские номера, номера
протоколов, показания (табличные цифры).

Принципы:
- Один главный поток на экране, одно главное действие — всегда в одном месте (справа сверху, на телефоне — снизу).
- Всё делается с клавиатуры: ⌘K / Ctrl+K — палитра команд и поиск по всему; `1…8` — режимы; `N` — новое в текущем режиме; `?` — подсказка клавиш; `Esc` — закрыть.
- Движение отвечает на действие: листы выезжают, строки подтверждаются вспышкой, переход между режимами — View Transitions; `prefers-reduced-motion` уважается.
- Пустой экран — приглашение к действию, ошибка — что случилось и что сделать.
- Скелетоны вместо спиннеров там, где известна форма данных.

## Правила для кода

- Новые общие компоненты — `frontend/src/ui/`; стили режима — свой файл `frontend/src/modes/<mode>/<mode>.css`.
- API режима — `frontend/src/modes/<mode>/api.ts` (общий `api.ts` только для общих вещей).
- Цвета только через токены.

## Оболочка и общие компоненты

Оболочка — `frontend/src/App.tsx`: боковая панель («Сегодня», «Режимы» 1…8, «Работа»,
«Справочники»; сворачивается до иконок, выбор помнится), шапка, таб-бар телефона
(4 режима по роли + «Ещё») и большая кнопка главного действия режима на телефоне.
Адрес — `#/<ключ>[/<хвост>]`; старые `#/field-work`, `#/protocol-templates`, `#/attestations`
перенаправляются. Тёмная тема — по системе или вручную (меню профиля), только для кабинета
(`<html data-app="cabinet">`); публичный сайт остаётся светлым.

### Реестр режимов — `modes/registry.ts`

```ts
type SectionDef = {
  key; title; short; icon; digit?: 1..8; group: "home" | "modes" | "work" | "reference";
  description;                       // одна строка — подзаголовок и строка в «Сегодня»
  visible?: (a: Access) => boolean;  // права: a.isManager / a.isControl / a.isReviewer / a.role
  component?: ComponentType<ModeProps>;   // обычно lazy(() => import("./<key>"))
  href?; flush?; keywords?;
  primary?: { label; icon; action; visible? };  // клавиша N, большая кнопка на телефоне
};
type ModeProps = { me; isManager; isControl; isReviewer; go: Go; refreshCounts: () => void };
type Go = (key: string, opts?: { trip?: number; action?: string; payload?: unknown; sub?: string }) => void;
```

Пересобрать режим = переписать `frontend/src/modes/<key>/index.tsx` (default export, пропсы
`ModeProps`). Сейчас там обёртки над старыми экранами (`Trips mode="city"`,
`Trips only="trips"|"procurements"`, `FieldWork`, `RequestsInbox`, `TemplateStudio`, `Attestations`).
Вспомогательные разделы — `modes/work/*.tsx`. `go("intake", { action: "new" })` — перейти и
сразу попросить режим сделать действие.

### Действия режима — `ui/modeActions.ts`

```ts
useModeAction("intake", { new: () => setCreating(true), "open-order": (id) => open(id as number) });
emitModeAction("intake", "new");        // палитра, клавиша N, кнопка на телефоне, плитка «Сегодня»
```
Если режим ещё грузится, действие ждёт его до 8 с. Имена, которые уже шлёт оболочка:
`city`: `new-visit`, `open-trip`; `trips`: `new-trip`, `open-trip`; `procurements`: `new`,
`new-procurement`, `open-procurement`; `intake`: `new`; `requests`: `open`, `search`;
`journal`: `search`. Новый режим должен их поддержать.

### Тосты — `ui/Toast.tsx`

```ts
toast.success("Приёмка создана", { action: { label: "Открыть", onClick } });
toast.error("Не удалось сохранить", { description: "Нет связи — повторите позже" });
toast.info("Заявка назначена Иванову", { undo: () => unassign(id) });   // кнопка «Отменить», 7 с
const id = toast.info("Загружаю…", { duration: 0 }); toast.success("Готово", { id });  // заменить
toast.dismiss(id);
```

### Палитра команд — `ui/CommandPalette.tsx` (⌘K / Ctrl+K, «/»)

```ts
useCommands("intake", [{ id: "intake:issue", title: "Выдать приборы", icon: "stamp", group: "Приёмки",
  keywords: "выдача акт", hint: "i", run: () => … }], [deps]);
registerCommandSource("orders", async (query, signal) => items, { group: "Приёмки", minQuery: 2, limit: 6 });
openCommandPalette("0123456");
```
`CommandItem = { id, title, subtitle?, icon?, group?, keywords?, hint?, mono?, run }`. Источники
по умолчанию (заявки, журнал — заводской номер/протокол, закупки, командировки) —
`modes/commandSources.ts`.

### Клавиши — `ui/Hotkeys.tsx`

```ts
useHotkey("n", create, { description: "Новая приёмка", group: "Приёмки" });
useHotkey(["j", "arrowdown"], next, { description: "Следующая заявка", group: "Заявки" });
useHotkey("mod+enter", save, { allowInInputs: true });
```
`mod` = ⌘/Ctrl; буквы ловятся и в русской раскладке (по `event.code`); в полях ввода и при открытом
окне клавиши без модификаторов молчат. Всё с `description` попадает в справку по «?».
Глобальные: `1…8` — режимы, `N` — главное действие, `?` — справка, ⌘K — палитра.

### Прочее

- `ui/Sheet.tsx` — `<Sheet open onClose title subtitle footer side="auto|right|bottom" size="sm|md|lg" modeless>`:
  справа на компьютере, снизу на телефоне; Esc, подложка, фокус внутри.
- `ui/EmptyState.tsx` — `<EmptyState icon title action={{ label, icon, onClick, hotkey }} secondary tone="error" compact>`;
  `action` можно и разметкой (как раньше). Старый импорт из `ui/controls` работает.
- `ui/Skeleton.tsx` — `<Skeleton width height radius/>`, `<SkeletonText lines/>`, `<SkeletonList rows/>`.
- `ui/Kbd.tsx` — `<Kbd keys="mod+k"/>` или `<Kbd>N</Kbd>`; класс `kbd-hint` прячет на сенсорных экранах.
- `ui/transition.ts` — `withViewTransition(() => setState(…))` (оболочка уже так меняет режимы),
  `flashOk(rowElement)` — зелёная вспышка подтверждённой строки (`.flash-ok`, `.flash-info`).
- Данные — классом `.data` (JetBrains Mono, табличные цифры); `.pass-text/.signal-text/.fail-text`.
- Новые иконки в `ui/Icon.tsx`: box, truck, map, route, building, handshake, contract, scan, barcode,
  keyboard, command, flask, thermometer, stamp, receipt, filter, home, sun, moon, history, clipboard,
  arrowLeft, arrowUpRight, collapse, expand, hash, target (были: phone, mail, globe, bolt, arrowRight…).
- Стили оболочки и общих компонентов — `ui/shell.css`; токены v2 и тёмная тема — начало `styles.css`.
