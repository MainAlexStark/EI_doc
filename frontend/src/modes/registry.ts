/** Реестр режимов и разделов оболочки (docs/rebuild.md, «Восемь режимов»).
 *
 * Чтобы пересобрать режим, замените его модуль: `frontend/src/modes/<key>/index.tsx`
 * (default export — компонент с пропсами ModeProps). Строку реестра трогать не нужно;
 * поменять название, иконку, права или главное действие — здесь, одной строкой. */

import { lazy } from "react";
import type { Access, SectionDef, SectionKey } from "./types";

const all = () => true;
const manager = (a: Access) => a.isManager;
const control = (a: Access) => a.isControl;

/** Публичный одностраничник фирмы (urls.py: path("o-nas/") — Landing). */
export const SITE_URL = "/o-nas/";

export const SECTIONS: SectionDef[] = [
  {
    key: "today", title: "Сегодня", short: "Сегодня", icon: "sun", group: "home",
    description: "Что ждёт сегодня: адреса, приёмки, заявки",
    component: lazy(() => import("./today")), keywords: "главная домой день",
  },

  // --- Режимы 1…8 --------------------------------------------------------
  {
    key: "city", title: "Город", short: "Город", icon: "route", digit: 1, group: "modes",
    description: "Выезды по Кирову и Яранску: адреса дня, окна, забор коробок",
    component: lazy(() => import("./city")),
    primary: { label: "Новое", icon: "plus", action: "new" },  // меню: Запись / Забрать коробку
    keywords: "выезды адреса день маршрут киров яранск забрать коробку",
  },
  {
    key: "trips", title: "Командировки", short: "Поездки", icon: "car", digit: 2, group: "modes",
    description: "Поездки на несколько дней: районы, сёла, адреса по дням, офлайн",
    component: lazy(() => import("./trips")),
    primary: { label: "Командировка", icon: "plus", action: "new" },
    keywords: "командировка поездка район село выезд",
  },
  {
    key: "procurements", title: "Закупки", short: "Закупки", icon: "contract", digit: 3, group: "modes",
    description: "Контракты 44/223-ФЗ: документы, требования, план-факт",
    component: lazy(() => import("./procurements")),
    primary: { label: "Закупка", icon: "plus", action: "new", visible: manager },
    keywords: "закупка контракт 44-фз 223-фз тз пропуск тендер",
  },
  {
    key: "intake", title: "Приёмки", short: "Приёмки", icon: "box", digit: 4, group: "modes",
    description: "Клиент принёс приборы: заказчик, список, акт, поверка, выдача",
    component: lazy(() => import("./intake")),
    primary: { label: "Приёмка", icon: "plus", action: "new" },
    keywords: "приём приемка коробка акт выдача лаборатория заказ",
  },
  {
    key: "site", title: "Сайт", short: "Сайт", icon: "globe", digit: 5, group: "modes",
    description: "Одностраничник фирмы с формой заявки — откроется в новой вкладке",
    href: SITE_URL, keywords: "сайт лендинг страница клиенты",
  },
  {
    key: "requests", title: "Заявки", short: "Заявки", icon: "inbox", digit: 6, group: "modes",
    description: "Входящие с сайта и по телефону: разбор на потоке, назначение",
    visible: manager, component: lazy(() => import("./requests")), flush: true,
    primary: { label: "Запись", icon: "phone", action: "new" },
    keywords: "заявка входящие сайт телефон назначить",
  },
  {
    key: "si-templates", title: "Шаблоны поверки СИ", short: "Шаблоны", icon: "layers", digit: 7, group: "modes",
    description: "Тип в ФИФ → методика → шаблон протокола вместе с ассистентом",
    component: lazy(() => import("./si-templates")), flush: true,
    primary: { label: "Новый шаблон", icon: "plus", action: "new" },
    keywords: "шаблон протокол методика фиф аршин тип си поверка метролог проверка",
  },
  {
    key: "att-templates", title: "Шаблоны аттестации", short: "Аттестация", icon: "shield", digit: 8, group: "modes",
    description: "Аттестация ИО по ГОСТ Р 8.568: план, формулы, протокол",
    visible: manager, component: lazy(() => import("./att-templates")),
    primary: { label: "Новый шаблон", icon: "plus", action: "new" },
    keywords: "аттестация испытательное оборудование ио гост 8.568 холодильник термостат шкаф камера план формулы",
  },

  // --- Работа ------------------------------------------------------------
  {
    key: "control", title: "Панель", short: "Панель", icon: "chart", group: "work",
    description: "Работа всех сотрудников за день", visible: control,
    component: lazy(() => import("./work/control")), keywords: "дашборд руководство сводка",
  },
  {
    key: "journal", title: "Журнал", short: "Журнал", icon: "doc", group: "work",
    description: "Протоколы поверки, нумерация, Аршин",
    component: lazy(() => import("./work/journal")), keywords: "протокол журнал нормоконтроль аршин",
  },
  {
    key: "availability", title: "График", short: "График", icon: "calendar", group: "work",
    description: "Кто когда может выезжать и чем занят",
    component: lazy(() => import("./work/availability")), flush: true, keywords: "календарь расписание смены",
  },
  {
    key: "tasks", title: "Задачи", short: "Задачи", icon: "tasks", group: "work",
    description: "Поручения и сроки", component: lazy(() => import("./work/tasks")), keywords: "задача поручение",
  },
  {
    key: "assistant", title: "Ассистент", short: "Ассистент", icon: "sparkle", group: "work",
    description: "Метролог-помощник: ФГИС «Аршин», методики, файлы компании",
    component: lazy(() => import("./work/assistant")), flush: true, keywords: "чат ии помощник",
  },

  // --- Справочники -------------------------------------------------------
  {
    key: "si-types", title: "Типы СИ", short: "Типы СИ", icon: "gauge", group: "reference",
    description: "Справочник средств измерений",
    component: lazy(() => import("./work/si-types")), keywords: "тип си справочник реестр",
  },
  {
    key: "staff", title: "Сотрудники", short: "Люди", icon: "users", group: "reference",
    description: "Учётные записи, роли, аттестация", visible: control,
    component: lazy(() => import("./work/staff")), keywords: "сотрудник пользователь роль",
  },
];

export const SECTION_BY_KEY = new Map(SECTIONS.map((section) => [section.key, section]));

/** Старые адреса → новые ключи (ссылки в закладках и в коде старых экранов). */
export const HASH_ALIASES: Record<string, SectionKey> = {
  "field-work": "intake",
  "protocol-templates": "si-templates",
  attestations: "att-templates",
};

export const isVisible = (section: SectionDef, access: Access) => (section.visible ?? all)(access);

/** Четыре режима в таб-баре телефона — по роли; остальное под «Ещё». */
export function phoneTabs(access: Access): SectionKey[] {
  if (access.isControl) return ["today", "requests", "intake", "city"];
  if (access.isManager) return ["today", "requests", "intake", "procurements"];
  return ["today", "city", "trips", "intake"];
}

/** Куда попадает человек без раздела в адресе. */
export const homeFor = (access: Access): SectionKey => (access.isControl ? "control" : "today");
