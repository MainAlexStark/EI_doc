/**
 * Работа поверителя без связи: локальный кэш (Dexie, db.ts) + очередь
 * исходящих операций (outbox), идентифицируемых client_id — тем же полем,
 * что уже есть у Verification на бэкенде (Verification.client_id,
 * см. apps/verification/models.py). Повторная отправка из очереди
 * безопасна: бэкенд просто вернёт уже созданную запись вместо дубля
 * (apps/verification/api_field.py).
 *
 * Очередь строго последовательная (FIFO), не параллельная — это не
 * оптимизация, а необходимость: измерения по поверке нельзя отправить,
 * пока сама поверка не создана на сервере (её id узнаём только из ответа
 * на create_verification), и порядок отправки — единственное, что эту
 * зависимость гарантирует без явного графа зависимостей между элементами
 * очереди.
 */
import {
  createVerification,
  fetchLayouts,
  fetchSiTypes,
  fetchTemplateForVerification,
  fetchVerificationTemplates,
  fetchWorkOrderVerifications,
  suggestSiTypes,
  submitMeasurements,
  type FieldVerification,
  type FieldVerificationPayload,
  type LayoutsResponse,
  type MeasurementsPayload,
  type MeasurementsResult,
  type SiType,
  type SiTypeSuggestion,
  type TemplateForVerification,
  type TemplateMeasurementsPayload,
  type VerificationTemplate,
} from "../api";
import { db, type CachedVerification } from "./db";
import { fitsYear } from "../registryYear";

function newUuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  // Резерв для контекстов без crypto.randomUUID — уникальности внутри
  // одного устройства достаточно, с серверными client_id такой не столкнётся.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

type Listener = () => void;
const listeners = new Set<Listener>();
export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function notify() {
  listeners.forEach((fn) => fn());
}

export function isOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine;
}

/**
 * navigator.onLine — не источник истины (известно врёт в ряде браузеров и
 * окружений), поэтому решение «в очередь или нет» принимается не по нему, а
 * по тому, ЧТО именно пошло не так при реальной попытке отправки. fetch()
 * бросает TypeError только при сетевой проблеме (нет соединения, DNS, CORS
 * и т. п.) — если сервер вообще ответил, пусть даже 400/500, это ApiError,
 * и глушить её как «нет связи» нельзя: пользователь решит, что его данные
 * повисли в очереди, хотя на самом деле сервер их отверг (например,
 * незаполненная строка измерений), и они там и останутся навсегда.
 */
function isNetworkFailure(exc: unknown): boolean {
  return exc instanceof TypeError;
}

function fromServer(v: FieldVerification): CachedVerification {
  return {
    client_id: v.client_id,
    work_order_id: v.work_order,
    server_id: v.id,
    si_type_id: v.si_type_id,
    si_type_name: v.instrument,
    serial_number: v.serial_number,
    manufacture_year: null,
    verified_at: v.verified_at,
    status: v.status,
    status_display: v.status_display,
    suitable: v.suitable,
    needs_review: v.needs_review,
    sync_state: "synced",
  };
}

// ---------------------------------------------------------------------------
// Поверки по наряду
// ---------------------------------------------------------------------------
export async function listVerifications(workOrderId: number): Promise<CachedVerification[]> {
  if (isOnline()) {
    try {
      const fresh = await fetchWorkOrderVerifications(workOrderId);
      await db.transaction("rw", db.verifications, async () => {
        for (const v of fresh) await db.verifications.put(fromServer(v));
      });
    } catch {
      // связь есть, а запрос не прошёл (сервер недоступен и т.п.) —
      // показываем то, что уже закэшировано, не роняем экран
    }
  }
  const cached = await db.verifications.where("work_order_id").equals(workOrderId).toArray();
  return cached.sort((a, b) => b.verified_at.localeCompare(a.verified_at));
}

export async function addVerification(
  workOrderId: number,
  payload: Omit<FieldVerificationPayload, "client_id">,
): Promise<CachedVerification> {
  const clientId = newUuid();
  const full: FieldVerificationPayload = { ...payload, client_id: clientId };

  // Пробуем отправить всегда, не только когда isOnline() говорит "да" — см.
  // isNetworkFailure(). В очередь уходим только если действительно нет сети;
  // если сервер ответил отказом (например, неверный si_type_id), это нужно
  // показать поверителю сразу, а не глушить как "нет связи".
  try {
    const created = await createVerification(workOrderId, full);
    const record = fromServer(created);
    await db.verifications.put(record);
    notify();
    return record;
  } catch (exc) {
    if (!isNetworkFailure(exc)) throw exc;
  }

  const optimistic: CachedVerification = {
    client_id: clientId,
    work_order_id: workOrderId,
    server_id: null,
    si_type_id: payload.si_type_id,
    si_type_name: await cachedSiTypeName(payload.si_type_id),
    serial_number: payload.serial_number,
    manufacture_year: payload.manufacture_year ?? null,
    verified_at: payload.verified_at ?? new Date().toISOString(),
    status: "draft",
    status_display: "черновик (не отправлено)",
    suitable: true,
    needs_review: false,
    sync_state: "pending",
  };
  await db.verifications.put(optimistic);
  await db.outbox.add({
    id: newUuid(),
    kind: "create_verification",
    work_order_id: workOrderId,
    verification_client_id: clientId,
    payload: full,
    created_at: Date.now(),
    attempts: 0,
  });
  notify();
  void flush();
  return optimistic;
}

// ---------------------------------------------------------------------------
// Измерения
// ---------------------------------------------------------------------------
export async function saveMeasurements(
  clientId: string,
  payload: MeasurementsPayload | TemplateMeasurementsPayload,
): Promise<{ queued: true } | { queued: false; result: MeasurementsResult }> {
  const cached = await db.verifications.get(clientId);
  const serverId = cached?.server_id ?? null;

  // Как и в addVerification — пробуем всегда, не по isOnline(); в очередь
  // падаем только на настоящей сетевой ошибке (isNetworkFailure), иначе
  // показываем реальный ответ сервера.
  if (serverId != null) {
    try {
      const result = await submitMeasurements(serverId, payload);
      await db.verifications.update(clientId, {
        status: result.status,
        suitable: result.suitable,
        needs_review: result.needs_review,
      });
      notify();
      return { queued: false, result };
    } catch (exc) {
      if (!isNetworkFailure(exc)) throw exc;
    }
  }

  await db.outbox.add({
    id: newUuid(),
    kind: "submit_measurements",
    verification_client_id: clientId,
    payload,
    created_at: Date.now(),
    attempts: 0,
  });
  notify();
  void flush();
  return { queued: true };
}

// ---------------------------------------------------------------------------
// Раскладки измерений — справочник, меняется редко, можно кэшировать широко
// ---------------------------------------------------------------------------
export async function loadLayouts(): Promise<LayoutsResponse> {
  if (isOnline()) {
    try {
      const fresh = await fetchLayouts();
      await db.meta.put({ key: "layouts", data: fresh, cached_at: Date.now() });
      return fresh;
    } catch {
      /* используем кэш ниже */
    }
  }
  const cached = await db.meta.get("layouts");
  if (cached) return cached.data as LayoutsResponse;
  throw new Error("Раскладки измерений недоступны офлайн — откройте этот экран хотя бы раз при связи");
}

// ---------------------------------------------------------------------------
// Шаблоны поверки (всё, кроме счётчиков воды): кэшируются, чтобы форма открывалась без связи
// ---------------------------------------------------------------------------
export async function refreshTemplateCache(): Promise<void> {
  try {
    const fresh = await fetchVerificationTemplates();
    await db.meta.put({ key: "verification_templates", data: fresh, cached_at: Date.now() });
  } catch {
    /* офлайн — остаётся прежняя копия */
  }
}

/** Шаблон без связи: свой у типа, иначе общий у семейства; параметры — из характеристик типа. */
async function offlineTemplateInfo(v: CachedVerification): Promise<TemplateForVerification> {
  const siType = (await cachedSiTypes()).find((item) => item.id === v.si_type_id);
  const list = ((await db.meta.get("verification_templates"))?.data as VerificationTemplate[] | undefined) ?? [];
  if (!siType || siType.calculator_key === "water_meter") return { engine: "water_meter", template: null };
  const template =
    list.find((t) => t.si_type === siType.id) ?? list.find((t) => t.family === siType.family && t.si_type == null);
  if (!template) return { engine: "water_meter", template: null };
  const limits = (siType.limits ?? {}) as unknown as Record<string, unknown>;
  const params: Record<string, string> = {};
  const fromType: string[] = [];
  for (const p of template.plan.params) {
    const value = p.from_type ? limits[p.from_type] : undefined;
    if (value !== undefined && value !== null && value !== "") {
      params[p.key] = String(value);
      fromType.push(p.key);
    }
  }
  return {
    engine: "template",
    template,
    params,
    params_from_type: fromType,
    missing: template.plan.params.filter((p) => !(p.key in params)).map((p) => p.key),
    // Без связи точки не пересчитать по формуле — показываем подпись из шаблона.
    modes: template.plan.modes.map((m) => ({
      key: m.key,
      label: (m.label || m.setpoint).replace("{setpoint}", m.setpoint).replace("{unit}", template.plan.unit),
      setpoint: m.setpoint,
    })),
    saved: null,
  };
}

/** Какой формой вносить измерения и по какому шаблону. params — пересчитать точки по внесённым параметрам. */
export async function loadTemplateInfo(
  v: CachedVerification,
  params?: Record<string, string>,
): Promise<TemplateForVerification & { offline?: boolean }> {
  const key = `tpl:${v.client_id}`;
  if (v.server_id != null) {
    try {
      const info = await fetchTemplateForVerification(v.server_id, params);
      if (!params) await db.meta.put({ key, data: info, cached_at: Date.now() });
      return info;
    } catch (exc) {
      if (!isNetworkFailure(exc)) throw exc;
    }
  }
  const cached = await db.meta.get(key);
  if (cached && !params) return { ...(cached.data as TemplateForVerification), offline: true };
  return { ...(await offlineTemplateInfo(v)), offline: true };
}

// ---------------------------------------------------------------------------
// Типы СИ: поиск работает и без связи — по справочнику, скачанному заранее
// ---------------------------------------------------------------------------
async function cachedSiTypes(): Promise<SiType[]> {
  const cached = await db.meta.get("si_types");
  return (cached?.data as SiType[] | undefined) ?? [];
}

/** Обновить локальную копию справочника типов. Тихо — без связи просто ничего не делаем. */
export async function refreshSiTypeCache(): Promise<void> {
  try {
    const fresh = await fetchSiTypes();
    await db.meta.put({ key: "si_types", data: fresh, cached_at: Date.now() });
  } catch {
    /* офлайн — остаётся прежняя копия */
  }
}

/** Добавить только что заведённый тип в локальную копию, не дожидаясь полного обновления. */
export async function rememberSiType(siType: SiType): Promise<void> {
  const list = (await cachedSiTypes()).filter((item) => item.id !== siType.id);
  await db.meta.put({ key: "si_types", data: [siType, ...list], cached_at: Date.now() });
}

async function cachedSiTypeName(id: number): Promise<string> {
  const found = (await cachedSiTypes()).find((item) => item.id === id);
  return found ? found.name : "";
}

function toSuggestion(item: SiType): SiTypeSuggestion {
  return {
    source: "local",
    si_type_id: item.id,
    registry_number: item.registry_number,
    name: item.name,
    manufacturer: item.manufacturer,
    notation: "",
    interval_months: item.verification_interval_months,
    label: item.label,
    ambiguous: false,
    needs_review: item.needs_review,
  };
}

export type SiTypeSearch = {
  results: SiTypeSuggestion[];
  offline: boolean;
  fifUnavailable: boolean;
  /** Сколько вариантов скрыто: не подходят по году выпуска прибора. */
  hiddenByYear: number;
  /** Почему реестр не ответил (или сервер EI_doc ответил ошибкой) — для индикатора «РСТ». */
  error?: string;
};

/** Поиск типа: при связи — свой справочник + реестр ФИФ, без связи — только локальная копия. */
export async function searchSiTypes(query: string, year: number | null = null): Promise<SiTypeSearch> {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return { results: [], offline: false, fifUnavailable: false, hiddenByYear: 0 };
  try {
    const response = await suggestSiTypes(query, { throwOnError: true, year });
    return {
      results: response.results,
      offline: false,
      fifUnavailable: Boolean(response.warning),
      hiddenByYear: response.hidden_by_year ?? 0,
      error: response.warning,
    };
  } catch (exc) {
    const matched = (await cachedSiTypes()).filter((item) =>
      [item.name, item.registry_number, item.manufacturer].some((field) => field.toLowerCase().includes(q)),
    );
    const fitting = fitsYear(matched, year);
    const hiddenByYear = matched.length - fitting.length;
    const local = fitting.slice(0, 20).map(toSuggestion);
    // Сервер ответил ошибкой — это не «нет связи», а сбой поиска: так и говорим.
    if (!isNetworkFailure(exc)) {
      const message = exc instanceof Error ? exc.message : "ошибка сервера";
      return { results: local, offline: false, fifUnavailable: true, hiddenByYear, error: `Поиск не сработал: ${message}` };
    }
    return { results: local, offline: true, fifUnavailable: true, hiddenByYear };
  }
}

// ---------------------------------------------------------------------------
// Очередь
// ---------------------------------------------------------------------------
export async function pendingCount(): Promise<number> {
  return db.outbox.count();
}

/** Элементы очереди, которые хотя бы раз не прошли (attempts > 0) — чтобы
 * показать поверителю не просто "в очереди: N", а что именно застряло и
 * почему, вместо молчаливого бесконечного retry. */
export async function outboxErrors(): Promise<import("./db").OutboxItem[]> {
  // attempts не входит в индексы схемы (см. db.ts) — очередь небольшая
  // (наряды одного поверителя), полный скан через filter() дешевле, чем
  // заводить лишний индекс ради этого.
  return db.outbox.filter((item) => item.attempts > 0).toArray();
}

/** Убрать застрявший элемент из очереди вручную — например, если данные в
 * нём были ошибочными и отправлять их больше не нужно. */
export async function removeOutboxItem(id: string): Promise<void> {
  await db.outbox.delete(id);
  notify();
}

let flushing = false;

/**
 * Разбирает очередь строго по одному элементу за раз, в порядке добавления.
 * На сетевой ошибке останавливается целиком (сервер всё равно недоступен —
 * нет смысла перебирать остальное). На ответе сервера с отказом (например,
 * не прошла валидация) — не блокирует независимые элементы очереди, просто
 * идёт дальше; сам отказавший элемент остаётся в очереди с last_error,
 * чтобы это было видно.
 */
export async function flush(): Promise<void> {
  // Не проверяем isOnline() здесь — она ненадёжна (см. isNetworkFailure) и
  // раньше из-за этого flush() мог тихо выходить, даже когда связь реально
  // есть: кнопка "Синхронизировать" выглядела как ничего не делающая. Сеть
  // проверяется по факту — первая же настоящая сетевая ошибка (TypeError)
  // останавливает цикл (см. ниже), лишний повторный удар по серверу не грозит.
  if (flushing) return;
  flushing = true;
  try {
    const items = await db.outbox.orderBy("created_at").toArray();
    for (const item of items) {
      if (!(await db.outbox.get(item.id))) continue; // уже разобрали конкурентным вызовом

      try {
        if (item.kind === "create_verification") {
          const created = await createVerification(item.work_order_id!, item.payload as FieldVerificationPayload);
          await db.verifications.put(fromServer(created));
        } else {
          const verification = await db.verifications.get(item.verification_client_id);
          if (!verification?.server_id) {
            // Родительская поверка ещё не синхронизирована — либо её
            // create-элемент раньше в очереди и до него дело дойдёт этим же
            // проходом, либо он сам застрял с ошибкой (тогда это видно по нему).
            continue;
          }
          const result = await submitMeasurements(verification.server_id, item.payload as MeasurementsPayload);
          await db.verifications.update(item.verification_client_id, {
            status: result.status,
            suitable: result.suitable,
            needs_review: result.needs_review,
          });
        }
        await db.outbox.delete(item.id);
        notify();
      } catch (exc) {
        await db.outbox.update(item.id, {
          attempts: item.attempts + 1,
          last_error: exc instanceof Error ? exc.message : "неизвестная ошибка",
        });
        notify();
        if (isNetworkFailure(exc)) break; // связи нет — остальное тоже не пройдёт, не долбим сервер зря
        // иначе сервер ответил отказом (не сеть) — не блокируем независимые
        // элементы очереди, идём дальше; этот элемент остаётся с last_error
      }
    }
  } finally {
    flushing = false;
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("online", () => void flush());
  window.addEventListener("focus", () => void flush());
  void flush();
}
