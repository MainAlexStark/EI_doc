/**
 * Клиент API EI_doc.
 *
 * Access-токен живёт 15 минут, refresh — 7 дней с ротацией. Поэтому запрос,
 * получивший 401, один раз обновляет пару токенов и повторяется: иначе
 * метролог будет разлогиниваться посреди нормоконтроля.
 */

const ACCESS = "ei_doc_access";
const REFRESH = "ei_doc_refresh";

export type JournalRow = {
  id: number;
  protocol_number: string;
  protocol_status: string;
  verified_at: string;
  next_verification_date: string | null;
  si_name: string;
  si_registry_number: string;
  serial_number: string;
  owner: string;
  address: string;
  verifier: string;
  verifier_id: number;
  suitable: boolean;
  status: string;
  needs_review: boolean;
  journal_note: string;
  protocol_formats: ("pdf" | "docx")[];
};

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };

export type Scope = {
  id: number;
  title: string;
  series: string;
  employee: string;
  year: number | null;
  sealed_high_water: number;
  drafts: number;
  numbered: number;
  signed: number;
  published: number;
  chronology_breaks: { previous: string; current: string; reason: string }[];
};

export type Preview = {
  changed: boolean;
  sealed_high_water: number;
  assigned: { verification: number; seq: number; verified_at: string }[];
  renumbered: { verification: number; from: number | null; to: number; verified_at: string }[];
  lines: string[];
};

export class ApiError extends Error {
  /** 0 — ошибка не от сервера (или статус неизвестен на месте вызова), не сетевая: см. offline/sync.ts. */
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

const tokens = {
  access: () => localStorage.getItem(ACCESS),
  refresh: () => localStorage.getItem(REFRESH),
  set(access: string, refresh?: string) {
    localStorage.setItem(ACCESS, access);
    if (refresh) localStorage.setItem(REFRESH, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  },
};

export const isAuthenticated = () => Boolean(tokens.access());
export const logout = () => tokens.clear();

async function refreshTokens(): Promise<boolean> {
  const refresh = tokens.refresh();
  if (!refresh) return false;

  const response = await fetch("/api/auth/token/refresh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!response.ok) {
    tokens.clear();
    return false;
  }
  const data = await response.json();
  tokens.set(data.access, data.refresh);
  return true;
}

export async function call(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  const access = tokens.access();
  if (access) headers.set("Authorization", `Bearer ${access}`);
  // FormData (загрузка фото скана) сама выставляет Content-Type с boundary —
  // подставлять application/json здесь нельзя, иначе сервер не разберёт multipart.
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && retry && (await refreshTokens())) {
    return call(path, init, false);
  }
  return response;
}

/** Ошибку DRF ({"поле": ["текст"]} / {"detail": "…"}) — в одну понятную строку,
 * без JSON-скобок на экране у поверителя. */
function humanError(body: unknown): string {
  if (body == null) return "";
  if (typeof body === "string") return body;
  if (Array.isArray(body)) return body.map(humanError).filter(Boolean).join("; ");
  if (typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (record.detail != null) return humanError(record.detail);
    return Object.values(record).map(humanError).filter(Boolean).join("; ");
  }
  return String(body);
}

export async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await call(path, init);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const body = await response.json();
      detail = humanError(body) || detail;
    } catch {
      /* тело не разобралось — оставим код */
    }
    throw new ApiError(detail, response.status);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function login(email: string, password: string): Promise<void> {
  const response = await fetch("/api/auth/token/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) throw new ApiError("Неверная почта или пароль");
  const data = await response.json();
  tokens.set(data.access, data.refresh);
}

export type JournalFilters = {
  q?: string;
  date_from?: string;
  date_to?: string;
  suitable?: string;
  needs_review?: string;
  /** Журнал одного поверителя; без него — общий. */
  verifier?: number;
  page?: number;
};

/** Поверитель, у которого есть свой журнал (хоть одна поверка). */
export type JournalVerifier = { id: number; full_name: string; count: number; is_me: boolean };

export const fetchJournalVerifiers = () => json<JournalVerifier[]>("/api/journal/verifiers/");

export function queryString(filters: Record<string, string | number | undefined | null>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "" && value !== null) params.set(key, String(value));
  }
  return params.toString();
}

export const fetchJournal = (filters: JournalFilters) =>
  json<Page<JournalRow>>(`/api/journal/?${queryString(filters)}`);

export const fetchScopes = () => json<Scope[]>("/api/normocontrol/scopes/");

export const previewNumbering = (scopeId: number) =>
  json<Preview>(`/api/normocontrol/scopes/${scopeId}/preview/`, { method: "POST" });

export const applyNumbering = (scopeId: number) =>
  json<{ assigned: number; renumbered: number; lines: string[] }>(
    `/api/normocontrol/scopes/${scopeId}/assign/`,
    { method: "POST" },
  );

/** Выгрузка журнала: файл отдаётся потоком, поэтому качаем через blob. */
export async function downloadJournal(filters: JournalFilters, verifierName = ""): Promise<void> {
  const response = await call(`/api/journal/export/?${queryString(filters)}`);
  if (!response.ok) throw new ApiError(`Не удалось выгрузить журнал (${response.status})`);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `Журнал учёта поверочных работ${verifierName ? ` — ${verifierName}` : ""}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Сотрудники (для выбора исполнителя)
// ---------------------------------------------------------------------------
export type Employee = { id: number; full_name: string; tab_number: string; position: string };

export const fetchEmployees = () => json<Employee[]>("/api/core/employees/");

// ---------------------------------------------------------------------------
// Текущий пользователь (для шапки — показать имя, а не только «Выйти»)
// ---------------------------------------------------------------------------
export type Me = {
  email: string;
  role: string;
  role_display: string;
  employee: {
    id: number;
    full_name: string;
    tab_number: string;
    position: string;
    telegram_linked: boolean;
  } | null;
};

export const fetchMe = () => json<Me>("/api/core/employees/me/");

// ---------------------------------------------------------------------------
// Подсказка адреса (заявка на сайте)
// ---------------------------------------------------------------------------
export type AddressSuggestion = {
  value: string;
  postal_code: string;
  fias_id: string;
  district: string;
  city: string;
  latitude: number | null;
  longitude: number | null;
};

export type AddressSuggestResponse = {
  configured: boolean;
  results: AddressSuggestion[];
  unavailable?: boolean;
};

/** Публичный эндпоинт — без токена, но через тот же call(), retry на 401 просто не сработает. */
export async function suggestAddress(query: string): Promise<AddressSuggestResponse> {
  const response = await call(`/api/hub/address-suggest/?q=${encodeURIComponent(query)}`);
  if (!response.ok) return { configured: false, results: [] };
  return response.json();
}

export type RequestItemPayload = { family_id: number; quantity: number };

export type RequestPayload = {
  contact_name: string;
  contact_phone?: string;
  contact_email?: string;
  address: string;
  postal_code?: string;
  fias_id?: string;
  district?: string;
  latitude?: number | null;
  longitude?: number | null;
  is_address_confirmed?: boolean;
  items?: RequestItemPayload[];
  si_description?: string;
  desired_date?: string | null;
  desired_time?: string | null;
  is_priority_slot?: boolean;
  comment?: string;
  consent_given: boolean;
  website?: string; // honeypot — держать пустым
  captcha_token?: string;
};

export async function submitRequest(payload: RequestPayload): Promise<{ id: number; status: string }> {
  const response = await fetch("/api/hub/requests/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.detail ?? "Не удалось отправить заявку");
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Диспетчерская: заявки
// ---------------------------------------------------------------------------
export type HubRequestItem = {
  id: number;
  family: string;
  family_id: number;
  quantity: number;
  unit_price: string;
  subtotal: string;
};

export type HubRequest = {
  id: number;
  source: string;
  status: string;
  status_display: string;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  address: string;
  district: string;
  items: HubRequestItem[];
  si_description: string;
  desired_date: string | null;
  desired_time: string | null;
  is_priority_slot: boolean;
  estimated_price: string | null;
  discount_percent: string;
  comment: string;
  suggested_employee: string;
  suggested_employee_id: number | null;
  assigned_employee: string;
  assigned_employee_id: number | null;
  reject_reason: string;
  is_address_confirmed: boolean;
  consent_given: boolean;
  created_at: string;
};

export type RequestFilters = { status?: string; district?: string; assigned_employee?: string };

export const fetchRequests = (filters: RequestFilters = {}) =>
  json<HubRequest[]>(`/api/hub/requests/list/?${queryString(filters)}`);

export const routeRequest = (id: number) =>
  json<HubRequest>(`/api/hub/requests/${id}/route/`, { method: "POST" });

export const confirmRequest = (
  id: number,
  payload: { employee_id: number; scheduled_date?: string | null; scheduled_time?: string | null; note?: string },
) => json<{ work_order_id: number }>(`/api/hub/requests/${id}/confirm/`, {
  method: "POST",
  body: JSON.stringify(payload),
});

export const rejectRequest = (id: number, payload: { reason?: string; spam?: boolean }) =>
  json<HubRequest>(`/api/hub/requests/${id}/reject/`, { method: "POST", body: JSON.stringify(payload) });

// ---------------------------------------------------------------------------
// Наряды
// ---------------------------------------------------------------------------
export type WorkOrder = {
  id: number;
  status: string;
  status_display: string;
  client: string;
  site: string;
  assigned_employee: string;
  assigned_employee_id: number;
  scheduled_date: string | null;
  scheduled_time: string | null;
  note: string;
  request_id: number | null;
  verifications_total: number;
  verifications_accepted: number;
  created_at: string;
  closed_at: string | null;
  /** Наряд — адрес выезда: в списке «Поверок» не показывается. */
  trip_id: number | null;
  trip_title: string;
  /** field — адрес выезда, lab — заказ: заказчик привёз приборы сам. */
  kind: "field" | "lab";
  client_id: number;
  client_inn: string;
  client_kind: string;
  client_address: string;
  client_phone: string;
  contact: string;
  received_at: string | null;
  issued_at: string | null;
  procurement_id: number | null;
  procurement_label: string;
  verifications_ready: number;
};

export type WorkOrderFilters = {
  status?: string;
  assigned_employee?: string;
  date_from?: string;
  date_to?: string;
  id?: string;
  trip?: string;
  kind?: string;
};

export const fetchWorkOrders = (filters: WorkOrderFilters = {}) =>
  json<WorkOrder[]>(`/api/work-orders/?${queryString(filters)}`);

export const setWorkOrderStatus = (id: number, status: string) =>
  json<WorkOrder>(`/api/work-orders/${id}/status/`, { method: "POST", body: JSON.stringify({ status }) });

// ---------------------------------------------------------------------------
// Задачи
// ---------------------------------------------------------------------------
export type Task = {
  id: number;
  title: string;
  description: string;
  parent: number | null;
  assignee: number | null;
  assignee_name: string;
  created_by: number | null;
  created_by_name: string;
  status: string;
  priority: string;
  due_date: string | null;
  estimated_hours: string | null;
  content_type: string | null;
  object_id: number | null;
  linked_label: string;
  recurrence: string;
  recurrence_parent: number | null;
  progress: number;
  children_count: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type TaskFilters = { status?: string; priority?: string; assignee?: string; mine?: string; parent?: string };

export const fetchTasks = (filters: TaskFilters = {}) =>
  json<Page<Task>>(`/api/tasks/?${queryString(filters)}`);

export const createTask = (payload: Partial<Task>) =>
  json<Task>("/api/tasks/", { method: "POST", body: JSON.stringify(payload) });

export const updateTask = (id: number, patch: Partial<Task>) =>
  json<Task>(`/api/tasks/${id}/`, { method: "PATCH", body: JSON.stringify(patch) });

export type TaskBoard = Record<string, Task[]>;
export const fetchTaskBoard = (assignee?: string) =>
  json<TaskBoard>(`/api/tasks/board/${assignee ? `?assignee=${assignee}` : ""}`);

export type TaskCalendar = Record<string, Task[]>;
export const fetchTaskCalendar = (dateFrom: string, dateTo: string) =>
  json<TaskCalendar>(`/api/tasks/calendar/?date_from=${dateFrom}&date_to=${dateTo}`);

export type Workload = {
  date_from: string;
  date_to: string;
  employees: { employee_id: number; employee: string; hours: number; count: number }[];
};
export const fetchWorkload = (dateFrom: string, dateTo: string) =>
  json<Workload>(`/api/tasks/workload/?date_from=${dateFrom}&date_to=${dateTo}`);

// ---------------------------------------------------------------------------
// Типы приборов и цены (форма заявки на сайте)
// ---------------------------------------------------------------------------
export type Family = {
  id: number;
  code: string;
  name: string;
  price: string;
  requires_time_slot: boolean;
};

export type FamilyOptionsResponse = { families: Family[]; priority_discount_percent: string };

/** Публичный эндпоинт, без токена — как suggestAddress. */
export async function fetchFamilies(): Promise<FamilyOptionsResponse> {
  const response = await fetch("/api/catalog/families/");
  if (!response.ok) return { families: [], priority_discount_percent: "0" };
  return response.json();
}

// ---------------------------------------------------------------------------
// Доступность сотрудников — публичные слоты для формы + личный график
// ---------------------------------------------------------------------------
export type PublicSlot = { date: string; start_time: string | null; end_time: string | null; is_priority: boolean };
export type PublicSlotsResponse = { district_known: boolean; slots: PublicSlot[] };

/** Публичный эндпоинт — район ещё не подтверждён диспетчером, поэтому передаём его строкой. */
export async function fetchPublicSlots(district: string): Promise<PublicSlotsResponse> {
  if (!district.trim()) return { district_known: false, slots: [] };
  const response = await fetch(`/api/hub/availability/slots/?${queryString({ district })}`);
  if (!response.ok) return { district_known: false, slots: [] };
  return response.json();
}

export type AvailabilitySlot = {
  id: number;
  employee: number;
  employee_name: string;
  kind: "district" | "trip";
  date: string;
  start_time: string | null;
  end_time: string | null;
  is_priority: boolean;
  note: string;
  created_at: string;
};

export type AvailabilityPayload = {
  kind: "district" | "trip";
  date: string;
  start_time?: string | null;
  end_time?: string | null;
  is_priority?: boolean;
  note?: string;
};

export const fetchMyAvailability = (filters: { date_from?: string; date_to?: string; employee?: number } = {}) =>
  json<Page<AvailabilitySlot>>(`/api/hub/availability/?${queryString(filters)}`);

/** Все страницы графика за период — в календаре месяц на нескольких людей легко больше 50 слотов. */
export async function fetchAvailabilityAll(
  filters: { date_from?: string; date_to?: string; employee?: number } = {},
): Promise<AvailabilitySlot[]> {
  const out: AvailabilitySlot[] = [];
  let page: Page<AvailabilitySlot> = await fetchMyAvailability(filters);
  out.push(...page.results);
  for (let guard = 0; page.next && guard < 20; guard += 1) {
    const next = new URL(page.next, window.location.origin);
    page = await json<Page<AvailabilitySlot>>(`${next.pathname}${next.search}`);
    out.push(...page.results);
  }
  return out;
}

export const createAvailability = (payload: AvailabilityPayload) =>
  json<AvailabilitySlot>("/api/hub/availability/", { method: "POST", body: JSON.stringify(payload) });

export const updateAvailability = (id: number, patch: Partial<AvailabilityPayload>) =>
  json<AvailabilitySlot>(`/api/hub/availability/${id}/`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteAvailability = (id: number) =>
  json<void>(`/api/hub/availability/${id}/`, { method: "DELETE" });

export type AvailabilityBulkPayload = {
  dates: string[];
  kind: "district" | "trip";
  start_time?: string | null;
  end_time?: string | null;
  is_priority?: boolean;
  note?: string;
};

/** Тот же слот сразу на несколько дат — чтобы не заполнять форму по одной дате за раз. */
export const bulkCreateAvailability = (payload: AvailabilityBulkPayload) =>
  json<AvailabilitySlot[]>("/api/hub/availability/bulk/", { method: "POST", body: JSON.stringify(payload) });

// ---------------------------------------------------------------------------
// Telegram — привязка личного чата
// ---------------------------------------------------------------------------
export type TelegramLinkInfo = {
  code: string;
  expires_at: string;
  bot_username: string;
  already_linked: boolean;
};

export const requestTelegramLinkCode = () =>
  json<TelegramLinkInfo>("/api/core/employees/me/telegram-link-code/", { method: "POST" });

// ---------------------------------------------------------------------------
// Капча (Yandex SmartCaptcha) — публичная форма заявки
// ---------------------------------------------------------------------------
export type CaptchaConfig = { configured: boolean; client_key: string };

/** Публичный эндпоинт, без токена — как fetchFamilies. */
export async function fetchCaptchaConfig(): Promise<CaptchaConfig> {
  const response = await fetch("/api/hub/captcha-config/");
  if (!response.ok) return { configured: false, client_key: "" };
  return response.json();
}

// ---------------------------------------------------------------------------
// Полевая работа поверителя: наряд → поверка → измерения
// ---------------------------------------------------------------------------
export type FieldVerification = {
  id: number;
  client_id: string;
  work_order: number;
  si_type_id: number;
  instrument: string;
  serial_number: string;
  verified_at: string;
  status: string;
  status_display: string;
  suitable: boolean;
  needs_review: boolean;
};

export const fetchWorkOrderVerifications = (workOrderId: number) =>
  json<FieldVerification[]>(`/api/work-orders/${workOrderId}/verifications/`);

export type FieldVerificationPayload = {
  client_id: string;
  si_type_id: number;
  serial_number: string;
  manufacture_year?: number | null;
  verified_at?: string;
};

export const createVerification = (workOrderId: number, payload: FieldVerificationPayload) =>
  json<FieldVerification>(`/api/work-orders/${workOrderId}/verifications/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export type SiTypeSuggestion = {
  source: "local" | "fif";
  si_type_id: number | null;
  registry_number: string;
  name: string;
  manufacturer: string;
  notation: string;
  interval_months: number | null;
  label: string;
  ambiguous: boolean;
  needs_review?: boolean;
  title?: string;
  fif_uuid?: string;
};

export type SiTypeSuggestResponse = {
  results: SiTypeSuggestion[];
  /** Сколько вариантов не показано, потому что не подходят по году выпуска. */
  hidden_by_year?: number;
  warning?: string;
  fif?: { ok: boolean; error: string; routes: string[] };
};

/** Связь с Госреестром (РСТ): какие пути живы (apps/arshin/status.py). */
export type FifRoute = {
  key: "mcp" | "direct";
  label: string;
  ok: boolean | null;
  ms: number | null;
  error: string;
  checked_at: string | null;
};
export type FifStatus = { ok: boolean; via: string | null; via_label: string; mode: string; routes: FifRoute[] };

export const fetchFifStatus = (probe = false) =>
  json<FifStatus>(`/api/catalog/fif/status/${probe ? "?probe=1" : ""}`);

/** Подсказка типов СИ — авторизованный эндпоинт, поэтому через json(), не суём в public-хелперы. */
export async function suggestSiTypes(
  query: string,
  options: { throwOnError?: boolean; year?: number | null } = {},
): Promise<SiTypeSuggestResponse> {
  if (query.trim().length < 2) return { results: [] };
  try {
    const params = options.year ? { q: query, year: String(options.year) } : { q: query };
    return await json<SiTypeSuggestResponse>(`/api/catalog/si-types/suggest/?${queryString(params)}`);
  } catch (exc) {
    if (options.throwOnError) throw exc;
    return { results: [] };
  }
}

export type LayoutMode = { mode: string; label: string; seconds: number };
export type LayoutsResponse = {
  layouts: Record<string, LayoutMode[]>;
  checks: Record<string, string>;
  classes: string[];
  common_unsuitability_reasons: string[];
};

export const fetchLayouts = () => json<LayoutsResponse>("/api/verifications/layouts/");

export type MeasurementRowPayload = {
  seconds?: number;
  flow_rate: string;
  volume_standard: string;
  reading_start?: string;
  reading_end?: string;
  pulses?: number | null;
  volume_meter?: string;
};

export type MeasurementsPayload = {
  layout: string;
  source?: string;
  meter_class: string;
  pulse_weight?: string;
  unit_type?: string;
  water_temperature?: string;
  checks?: Record<string, boolean>;
  rows: MeasurementRowPayload[];
  /** Непригоден не по расчётной погрешности (осмотр, повреждение и т. п.) —
   * тогда rows может быть пустым, прибор физически не проверить. */
  manual_unsuitable?: boolean;
  manual_unsuitability_reason?: string;
};

export type MeasurementsResult = {
  suitable: boolean;
  rows: Record<string, unknown>[];
  failed_rows: number[];
  reasons: string[];
  journal_note: string;
  needs_review: boolean;
  status: string;
};

// ---------------------------------------------------------------------------
// Поверка по шаблону (всё, кроме счётчиков воды) — claude/templates.md
// ---------------------------------------------------------------------------
export type TemplateParam = { key: string; label: string; unit: string; from_type: string; sample: string; source: string };
export type TemplateOperation = { key: string; label: string; source: string };

export type VerificationTemplate = {
  id: number;
  title: string;
  family: number;
  family_name: string;
  si_type: number | null;
  si_type_name: string;
  needs_review: boolean;
  is_active: boolean;
  updated_at: string;
  created_via_assistant: boolean;
  note: string;
  kits?: { id: number; section: string; scope: string; standards_text: string; employee_name: string }[];
  plan: {
    si_kind: string;
    method: string;
    quantity: string;
    unit: string;
    repeats: number;
    params: TemplateParam[];
    operations: TemplateOperation[];
    modes: { key: string; label: string; setpoint: string }[];
    points: { key: string; label: string }[];
    characteristics: { key: string; label: string; text: string; per: string }[];
    extra_conditions: { key: string; label: string; unit: string }[];
    uses_display: boolean;
  };
  sources?: string[];
  created_by?: string;
  reviewed_by?: string;
};

export type TemplateReadings = Record<string, { readings: Record<string, string[]>; display?: string }>;

export type TemplateForVerification = {
  engine: "template" | "water_meter";
  template: VerificationTemplate | null;
  kits?: TemplateKit[];
  params?: Record<string, string>;
  params_from_type?: string[];
  missing?: string[];
  modes?: { key: string; label: string; setpoint: string }[];
  saved?: {
    kind?: string;
    operations?: { key: string; label: string; passed: boolean | null }[];
    readings?: TemplateReadings;
    conditions?: Record<string, string>;
    manual_unsuitable?: boolean;
    manual_unsuitability_reason?: string;
    kit?: { id: number; section: string; scope: string } | null;
  } | null;
};

/** Строка перечня эталонов, из которой поверитель выбирает, что брал на поверку. */
export type TemplateKit = {
  id: number;
  section: string;
  scope: string;
  standards_text: string;
  aux_text: string;
  employee_name: string;
  own: boolean;
};

export type TemplateMeasurementsPayload = {
  template_id?: number;
  kind: "periodic" | "primary";
  params: Record<string, string>;
  operations: Record<string, boolean>;
  readings: TemplateReadings;
  conditions?: Record<string, string>;
  manual_unsuitable?: boolean;
  manual_unsuitability_reason?: string;
  kit?: number | null;
};

export const fetchVerificationTemplates = (all = false) =>
  json<VerificationTemplate[]>(`/api/verification-templates/${all ? "?all=1" : ""}`);

export const fetchVerificationTemplate = (id: number) =>
  json<VerificationTemplate>(`/api/verification-templates/${id}/`);

export const approveVerificationTemplate = (id: number) =>
  json<VerificationTemplate>(`/api/verification-templates/${id}/approve/`, { method: "POST" });

export const fetchTemplateForVerification = (verificationId: number, params?: Record<string, string>) =>
  params
    ? json<TemplateForVerification>(`/api/verifications/${verificationId}/template/`, {
        method: "POST",
        body: JSON.stringify({ params }),
      })
    : json<TemplateForVerification>(`/api/verifications/${verificationId}/template/`);

/** Скачать файл с защищённого эндпоинта (токен — в заголовке, поэтому не голая ссылка). */
async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const response = await call(path);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      detail = humanError(await response.json()) || detail;
    } catch {
      /* не JSON */
    }
    throw new ApiError(detail, response.status);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? decodeURIComponent(match[1]) : fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Протокол поверки: черновик, пока не подписан; по шаблону — Word или PDF, счётчики воды — PDF. */
export const downloadVerificationProtocol = (verificationId: number, fmt: "pdf" | "docx") =>
  downloadFile(`/api/verifications/${verificationId}/protocol.${fmt}`, `Протокол.${fmt}`);

export const downloadTemplatePreview = (templateId: number, fmt: "pdf" | "docx") =>
  downloadFile(`/api/verification-templates/${templateId}/preview.${fmt}`, `Шаблон протокола.${fmt}`);

export const submitMeasurements = (verificationId: number, payload: MeasurementsPayload | TemplateMeasurementsPayload) =>
  json<MeasurementsResult>(`/api/verifications/${verificationId}/measurements/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

// ---------------------------------------------------------------------------
// Бланк с QR и распознавание (второй срез офлайна, claude/scans.md)
// ---------------------------------------------------------------------------

/** Скачать печатный бланк — открывает PDF в новой вкладке (protected-эндпоинт,
 * поэтому не голая ссылка: токен нужно подставить в заголовок). */
export async function downloadBlank(workOrderId: number, copies = 1): Promise<void> {
  const response = await call(`/api/work-orders/${workOrderId}/blank/?${queryString({ copies })}`);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* PDF-эндпоинт при ошибке тоже отдаёт JSON — но на всякий случай */
    }
    throw new ApiError(detail, response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export type ScanRecognizedRow = {
  flow_rate?: string | null;
  reading_start?: string | null;
  reading_end?: string | null;
  volume_standard?: string | null;
  confidence?: number | null;
};

export type ScanRecognized = {
  legible?: boolean;
  si_type_query?: string | null;
  serial_number?: string | null;
  manufacture_year?: number | null;
  unit_type?: "hot" | "cold" | null;
  meter_class?: "A" | "B" | null;
  pulse_weight?: string | null;
  water_temperature?: string | null;
  checks?: { visual?: boolean | null; operation?: boolean | null; tightness?: boolean | null };
  manual_unsuitable?: boolean | null;
  manual_unsuitability_reason?: string | null;
  rows?: ScanRecognizedRow[];
};

export type ScanUploadResult = {
  id: number;
  work_order: number;
  verification: number | null;
  created_at: string;
  error: string;
  recognized: ScanRecognized;
  image_url: string;
  warning?: string;
};

export const fetchWorkOrderScans = (workOrderId: number) =>
  json<ScanUploadResult[]>(`/api/work-orders/${workOrderId}/scans/`);

/** Фото бланка → черновик распознавания. Поверку ещё не заводит — см. applyScan(). */
export async function uploadScan(workOrderId: number, file: File): Promise<ScanUploadResult> {
  const body = new FormData();
  body.append("photo", file);
  return json<ScanUploadResult>(`/api/work-orders/${workOrderId}/scans/`, { method: "POST", body });
}

export const fetchScan = (scanId: number) => json<ScanUploadResult>(`/api/scans/${scanId}/`);

/** Фото скана как blob-URL — эндпоинт защищён токеном, обычный <img src> его не подставит. */
export async function fetchScanImageUrl(scanId: number): Promise<string> {
  const response = await call(`/api/scans/${scanId}/image/`);
  if (!response.ok) throw new ApiError(`Не удалось загрузить фото (${response.status})`, response.status);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export type ScanApplyPayload = {
  si_type_id: number;
  serial_number: string;
  manufacture_year?: number | null;
  measurements: MeasurementsPayload;
};

/** Экран сверки подтверждён человеком → заводим поверку. Идемпотентно по scan.id. */
export const applyScan = (scanId: number, payload: ScanApplyPayload) =>
  json<FieldVerification>(`/api/scans/${scanId}/apply/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

// ---------------------------------------------------------------------------
// Фото поверяемого СИ (по одной поверке — сколько угодно)
// ---------------------------------------------------------------------------
export type VerificationPhoto = {
  id: number;
  verification: number;
  caption: string;
  uploaded_by: string;
  created_at: string;
  image_url: string;
};

export const fetchVerificationPhotos = (verificationId: number) =>
  json<VerificationPhoto[]>(`/api/verifications/${verificationId}/photos/`);

/** Требует, чтобы поверка уже была на сервере — офлайн-черновик (без server_id) фото пока не принимает. */
export async function uploadVerificationPhoto(
  verificationId: number,
  file: File,
  caption = "",
): Promise<VerificationPhoto> {
  const body = new FormData();
  body.append("photo", file);
  if (caption) body.append("caption", caption);
  return json<VerificationPhoto>(`/api/verifications/${verificationId}/photos/`, { method: "POST", body });
}

export const deleteVerificationPhoto = (photoId: number) =>
  json<void>(`/api/verification-photos/${photoId}/`, { method: "DELETE" });

/** Фото как blob-URL — эндпоинт защищён токеном, обычный <img src> его не подставит. */
export async function fetchVerificationPhotoUrl(photoId: number): Promise<string> {
  const response = await call(`/api/verification-photos/${photoId}/image/`);
  if (!response.ok) throw new ApiError(`Не удалось загрузить фото (${response.status})`, response.status);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

// ---------------------------------------------------------------------------
// Справочник типов СИ без админки (apps/catalog/api_types.py)
// ---------------------------------------------------------------------------
export type WaterLimits = Record<string, number | string>;

export type SiType = {
  id: number;
  family: number;
  family_name: string;
  calculator_key: string;
  registry_number: string;
  name: string;
  manufacturer: string;
  verification_interval_months: number;
  limits: WaterLimits;
  fif_url: string;
  fif_uuid: string;
  type_title: string;
  certificate_valid_to: string | null;
  needs_review: boolean;
  created_by_name: string;
  reviewed_by_name: string;
  reviewed_at: string | null;
  label: string;
  existing?: boolean;
  template_job?: TemplateJob | null;
  templates?: { id: number; title: string; needs_review: boolean; own: boolean; kits: string[] }[];
};

/** Работа агента: шаблон поверки (и аттестации для ИО) для нового типа (apps/assistant/template_jobs.py). */
export type TemplateJob = {
  id: number;
  status: "queued" | "running" | "done" | "failed" | "skipped";
  status_display: string;
  comment: string;
  summary: string;
  error: string;
  created_at: string;
  finished_at: string | null;
  verification_template: { id: number; title: string; needs_review: boolean } | null;
  attestation_program: { id: number; title: string; needs_review: boolean } | null;
  session_id: string;
};

export const fetchTemplateJobs = (siTypeId: number) =>
  json<TemplateJob[]>(`/api/catalog/si-types/${siTypeId}/template-jobs/`);

export const startTemplateJob = (siTypeId: number, comment = "") =>
  json<TemplateJob>(`/api/catalog/si-types/${siTypeId}/template-jobs/`, {
    method: "POST",
    body: JSON.stringify({ comment }),
  });

// ---------------------------------------------------------------------------
// «Перечень применяемых эталонов» (apps/catalog/api_kits.py)
// ---------------------------------------------------------------------------
export type StandardKit = {
  id: number;
  section: string;
  scope: string;
  employee: number | null;
  employee_name: string;
  standards_text: string;
  aux_text: string;
  journal_aux: string;
  fif_numbers: string[];
  families: number[];
  family_names: string[];
  note: string;
  sort: number;
  is_active: boolean;
};

export type AuxInstrument = { id: number; name: string; designation: string; note: string; is_active: boolean };

export type StandardKitsResponse = { kits: StandardKit[]; aux: AuxInstrument[]; can_edit: boolean };

export const fetchStandardKits = (all = false) =>
  json<StandardKitsResponse>(`/api/catalog/standard-kits/${all ? "?all=1" : ""}`);

export const saveStandardKit = (id: number | null, payload: Partial<StandardKit>) =>
  id
    ? json<StandardKit>(`/api/catalog/standard-kits/${id}/`, { method: "PATCH", body: JSON.stringify(payload) })
    : json<StandardKit>("/api/catalog/standard-kits/", { method: "POST", body: JSON.stringify(payload) });

export const saveAuxInstrument = (id: number | null, payload: Partial<AuxInstrument>) =>
  id
    ? json<AuxInstrument>(`/api/catalog/aux-instruments/${id}/`, { method: "PATCH", body: JSON.stringify(payload) })
    : json<AuxInstrument>("/api/catalog/aux-instruments/", { method: "POST", body: JSON.stringify(payload) });

export async function importStandardKits(file: File) {
  const body = new FormData();
  body.append("file", file);
  return json<{ kits_created: number; kits_updated: number; kits_disabled: number; aux: number; aux_disabled: number }>(
    "/api/catalog/standard-kits/import/",
    { method: "POST", body },
  );
}

export type SiTypeInput = {
  family?: number | null;
  registry_number: string;
  name: string;
  manufacturer: string;
  verification_interval_months?: number | null;
  limits?: WaterLimits;
  fif_uuid?: string;
  type_title?: string;
  certificate_valid_to?: string | null;
};

export type LimitPreset = {
  key: string;
  dn: number | null;
  label: string;
  detail: string;
  count: number;
  examples: string[];
  limits: Record<string, number>;
};

export type SiTypePresets = {
  fields: { key: string; label: string }[];
  presets: LimitPreset[];
  families: { id: number; name: string; calculator_key: string }[];
  default_family: number | null;
  can_review: boolean;
  can_manual: boolean;
};

export const fetchSiTypes = (params: { needs_review?: boolean; q?: string } = {}) =>
  json<SiType[]>(
    `/api/catalog/si-types/?${queryString({
      needs_review: params.needs_review ? "1" : undefined,
      q: params.q || undefined,
    })}`,
  );

export const fetchSiTypePresets = () => json<SiTypePresets>("/api/catalog/si-types/presets/");

export const createSiType = (payload: SiTypeInput) =>
  json<SiType>("/api/catalog/si-types/", { method: "POST", body: JSON.stringify(payload) });

export const updateSiType = (id: number, payload: Partial<SiTypeInput>) =>
  json<SiType>(`/api/catalog/si-types/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

export const confirmSiType = (id: number) =>
  json<SiType>(`/api/catalog/si-types/${id}/confirm/`, { method: "POST" });

// ---------------------------------------------------------------------------
// Реестр ФИФ (РСТ, ФГИС «Аршин»): карточка типа, документы, чтение описания
// (apps/catalog/api_fif.py)
// ---------------------------------------------------------------------------
export type FifDocument = {
  id: number;
  kind: "meth" | "spec";
  kind_display: string;
  title: string;
  url: string;
  downloaded: boolean;
  size: number | null;
  error: string;
  file_url: string;
};

export type FifCard = {
  uuid: string;
  registry_number: string;
  title: string;
  notation: string;
  variants: string[];
  manufacturer: string;
  valid_to: string | null;
  is_valid: boolean | null;
  mpi: { parameter: string; months: number; valid_from: string }[];
  documents: FifDocument[];
  already_added: { id: number; name: string; needs_review: boolean }[];
  limits_reader: boolean;
  family_guess: number | null;
};

export type FifLimitsResult = {
  found: boolean;
  limits: Record<string, number>;
  missing: string[];
  dn: number | null;
  variant_matched: string | null;
  quote: string;
  comment: string;
  mode: "text" | "images";
};

export const fetchFifCard = (ident: string) =>
  json<FifCard>(`/api/catalog/fif/types/${encodeURIComponent(ident)}/`);

export const readFifLimits = (ident: string, variant: string) =>
  json<FifLimitsResult>(`/api/catalog/fif/types/${encodeURIComponent(ident)}/limits/`, {
    method: "POST",
    body: JSON.stringify({ variant }),
  });

export const fetchSiTypeDocuments = (siTypeId: number) =>
  json<FifDocument[]>(`/api/catalog/si-types/${siTypeId}/documents/`);

/** Открыть документ типа в новой вкладке: свой файл — с авторизацией,
 * а если скачать к себе не вышло — ссылкой в ФГИС. Вкладку открываем сразу,
 * синхронно с нажатием, иначе мобильный браузер её заблокирует. */
export async function openFifDocument(doc: FifDocument): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const response = await call(doc.file_url);
    if (response.ok) {
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      if (tab) tab.location.href = url;
      else window.location.href = url;
      return;
    }
    let target = doc.url;
    try {
      target = (await response.json()).url || doc.url;
    } catch {
      /* тело не JSON — остаётся ссылка из карточки */
    }
    if (tab) tab.location.href = target;
  } catch (exc) {
    tab?.close();
    throw exc;
  }
}

// ---------------------------------------------------------------------------
// Прошлые поверки СИ по заводскому номеру (apps/verification/api_history.py)
// ---------------------------------------------------------------------------
export type TypeBrief = { id: number; name: string; label: string; needs_review: boolean };

/** Экземпляр, который мы уже поверяли. */
export type OwnInstrumentHistory = {
  instrument_id: number;
  serial_number: string;
  manufacture_year: number | null;
  si_type: TypeBrief;
  registry_number: string;
  owner: string;
  site: string;
  verifications_count: number;
  last: {
    verification_id: number;
    verified_at: string | null;
    next_verification_date: string | null;
    suitable: boolean;
    status: string;
    verifier: string;
    protocol_number: string;
  } | null;
};

/** Запись о поверке из ФГИС «Аршин» и наши типы по её номеру Госреестра. */
export type FgisVerification = {
  vri_id: string;
  org_title: string;
  mit_number: string;
  mit_title: string;
  mit_notation: string;
  modification: string;
  mi_number: string;
  verification_date: string | null;
  valid_date: string | null;
  docnum: string;
  applicable: boolean | null;
  /** Наш тип, если совпали номер Госреестра и модификация. */
  si_type: TypeBrief | null;
  candidates: TypeBrief[];
};

export type FgisHistory = {
  ok: boolean;
  error: string;
  items: FgisVerification[];
  partial: boolean;
  years: number[];
};

export type FgisVerificationCard = {
  vri_id: string;
  mit_number: string;
  mit_title: string;
  mit_notation: string;
  mit_uuid: string;
  modification: string;
  mi_number: string;
  manufacture_year: number | null;
  organization: string;
  owner: string;
  verification_date: string | null;
  valid_date: string | null;
  method: string;
  applicable: boolean | null;
  docnum: string;
  additional_info: string;
  card_url: string;
  si_type: TypeBrief | null;
  candidates: TypeBrief[];
};

/** Своя база отвечает сразу, ФГИС — секунд пять-десять, поэтому спрашиваем их по отдельности. */
export const fetchOwnHistory = (serial: string) =>
  json<{ serial: string; own: OwnInstrumentHistory[] }>(
    `/api/instruments/history/?source=own&serial=${encodeURIComponent(serial)}`,
  );

export const fetchFgisHistory = (serial: string) =>
  json<{ serial: string; fgis: FgisHistory }>(
    `/api/instruments/history/?source=fgis&serial=${encodeURIComponent(serial)}`,
  );

export const fetchFgisVerification = (vriId: string) =>
  json<FgisVerificationCard>(`/api/instruments/history/fgis/${encodeURIComponent(vriId)}/`);

// ---------------------------------------------------------------------------
// Командировки и закупки (apps/logistics/api.py, claude/trips.md)
// ---------------------------------------------------------------------------
export type PersonRef = { id: number; full_name: string };

export type ProcurementBrief = {
  id: number;
  label: string;
  number: string;
  law: string;
  law_display: string;
  title: string;
  customer: string;
  contract_number: string;
  contract_date: string | null;
  deadline: string | null;
  status: string;
  status_display: string;
};

export type TripBrief = {
  id: number;
  kind: "city" | "classic" | "procurement";
  kind_display: string;
  title: string;
  destination: string;
  date_start: string | null;
  date_end: string | null;
  status: "planned" | "active" | "done" | "cancelled";
  status_display: string;
  lead: PersonRef;
  members: PersonRef[];
  note: string;
  procurement: ProcurementBrief | null;
  stops_total: number;
  stops_done: number;
  instruments_entered: number;
};

export type StopStatus = "planned" | "done" | "no_answer" | "postponed" | "refused";

export type TripStop = {
  id: number;
  position: number;
  status: StopStatus;
  status_display: string;
  site_id: number;
  address: string;
  latitude: number | null;
  longitude: number | null;
  is_restricted: boolean;
  access_note: string;
  client: string;
  contact_name: string;
  contact_phone: string;
  planned_date: string | null;
  planned_count: number | null;
  note: string;
  added_on_site: boolean;
  carried_from: string;
  visited_at: string | null;
  work_order_id: number | null;
  verifications_total: number;
  verifications_ready: number;
  verifications_unsuitable: number;
  window_start: string | null;
  window_end: string | null;
  service_minutes: number | null;
  eta: string | null;
  drive_minutes: number | null;
};

export type TripSummary = {
  stops_total: number;
  stops_done: number;
  stops_waiting: number;
  stops_no_answer: number;
  stops_postponed: number;
  stops_refused: number;
  stops_on_site: number;
  instruments_planned: number;
  instruments_entered: number;
  instruments_ready: number;
  instruments_unsuitable: number;
};

export type PassCoverage = { employee_id: number; full_name: string; state: "ok" | "pending" | "missing"; text: string };

export type Trip = TripBrief & {
  stops: TripStop[];
  summary: TripSummary;
  can_edit: boolean;
  can_locate: boolean;
  visit_number: number | null;
  passes: PassCoverage[];
  route: RouteSummary | null;
  /** Есть ключ 2ГИС — время в пути с пробками. */
  routing_traffic: boolean;
  /** Задан адрес офиса — можно строить маршрут «из офиса». */
  has_base: boolean;
  /** Рабочий день по умолчанию, «08:00»–«17:00». */
  work_day: [string, string];
};

export type RouteSummary = {
  planned_at: string;
  departure: string;
  finish: string;
  origin: string;
  provider: "2gis" | "estimate";
  provider_label: string;
  traffic: boolean;
  notes: string[];
  drive_minutes: number;
  km: number;
  stops: number;
  late: number;
  after_day_end: number;
  service_minutes: number;
  per_instrument_minutes: number;
  day_start: string;
  day_end: string;
  return_to_start: boolean;
  days: string[];
  multi_day: boolean;
};

export type RouteRow = {
  id: number;
  day: string;
  address: string;
  eta: string;
  leave: string;
  drive_minutes: number;
  km: number;
  wait_minutes: number;
  late_minutes: number;
  after_day_end: boolean;
  window: [string | null, string | null];
};

export type PlanPayload = {
  start: "base" | "me" | "first";
  latitude?: number;
  longitude?: number;
  departure?: string;
  service_minutes: number;
  per_instrument_minutes: number;
  day_start: string;
  day_end: string;
  return_to_start: boolean;
  multi_day?: boolean;
  apply?: boolean;
};

export type TripPayload = {
  kind?: "city" | "classic" | "procurement";
  title?: string;
  destination?: string;
  date_start?: string | null;
  date_end?: string | null;
  status?: string;
  lead_id?: number;
  member_ids?: number[];
  procurement_id?: number | null;
  note?: string;
};

export type StopPayload = {
  address?: string;
  site_id?: number | null;
  client_name?: string;
  contact_name?: string;
  contact_phone?: string;
  latitude?: number | null;
  longitude?: number | null;
  postal_code?: string;
  fias_id?: string;
  district?: string;
  planned_date?: string | null;
  planned_count?: number | null;
  note?: string;
  is_restricted?: boolean;
  access_note?: string;
  added_on_site?: boolean;
  status?: StopStatus;
  window_start?: string | null;
  window_end?: string | null;
  service_minutes?: number | null;
};

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const patch = (body: unknown): RequestInit => ({ method: "PATCH", body: JSON.stringify(body) });

export const fetchTrips = (filters: { state?: "open" | "closed"; procurement?: number } = {}) =>
  json<TripBrief[]>(`/api/trips/?${queryString(filters)}`);
export const fetchTrip = (id: number) => json<Trip>(`/api/trips/${id}/`);
export const createTrip = (payload: TripPayload) => json<Trip>("/api/trips/", post(payload));
export const updateTrip = (id: number, payload: TripPayload) => json<Trip>(`/api/trips/${id}/`, patch(payload));
export const deleteTrip = (id: number) => json<void>(`/api/trips/${id}/`, { method: "DELETE" });
export const addTripStop = (tripId: number, payload: StopPayload) =>
  json<TripStop>(`/api/trips/${tripId}/stops/`, post(payload));
export const addTripStopsBulk = (tripId: number, text: string) =>
  json<{ added: number; skipped: string[]; trip: Trip }>(`/api/trips/${tripId}/stops/bulk/`, post({ text }));
export const reorderTripStops = (tripId: number, order: number[]) =>
  json<Trip>(`/api/trips/${tripId}/stops/reorder/`, post({ order }));
export const locateTripStops = (tripId: number) =>
  json<{ found: number; not_found: number; remaining: number; trip: Trip }>(`/api/trips/${tripId}/locate/`, post({}));
export const startNextVisit = (tripId: number, payload: TripPayload) =>
  json<Trip>(`/api/trips/${tripId}/next-visit/`, post(payload));
export const planTripRoute = (tripId: number, payload: PlanPayload) =>
  json<{ summary: RouteSummary; rows: RouteRow[]; trip?: Trip }>(`/api/trips/${tripId}/plan/`, post(payload));
export const updateTripStop = (id: number, payload: StopPayload) =>
  json<TripStop>(`/api/trip-stops/${id}/`, patch(payload));
export const deleteTripStop = (id: number) => json<void>(`/api/trip-stops/${id}/`, { method: "DELETE" });

export type ProcurementProgress = { planned: number; entered: number; ready: number; unsuitable: number };
export type ProcurementRow = ProcurementBrief & { trips_total: number; progress: ProcurementProgress };
export type ProcurementItem = { id?: number; title: string; quantity: number };
export type ProcurementDocument = {
  id: number;
  kind: string;
  kind_display: string;
  title: string;
  filename: string;
  size: number;
  uploaded_by: string;
  created_at: string;
};
export type ProcurementPass = {
  id: number;
  employee: PersonRef | null;
  vehicle: string;
  holder: string;
  site_id: number | null;
  site: string;
  number: string;
  valid_from: string | null;
  valid_to: string | null;
  status: "needed" | "requested" | "issued" | "refused";
  status_display: string;
  note: string;
};
export type Procurement = ProcurementBrief & {
  customer_id: number;
  customer_inn: string;
  date_start: string | null;
  amount: string | null;
  eis_url: string;
  open_url: string;
  responsible: PersonRef | null;
  contact: string;
  note: string;
  items: ProcurementItem[];
  documents: ProcurementDocument[];
  passes: ProcurementPass[];
  sites: { id: number; address: string; is_restricted: boolean }[];
  trips: TripBrief[];
  progress: ProcurementProgress;
  can_edit: boolean;
};
export type ProcurementPayload = {
  number?: string;
  law?: string;
  title?: string;
  customer_name?: string;
  customer_inn?: string;
  contract_number?: string;
  contract_date?: string | null;
  date_start?: string | null;
  deadline?: string | null;
  amount?: string | null;
  eis_url?: string;
  status?: string;
  contact?: string;
  note?: string;
  items?: ProcurementItem[];
};
export type PassPayload = {
  employee_id?: number | null;
  vehicle?: string;
  site_id?: number | null;
  number?: string;
  valid_from?: string | null;
  valid_to?: string | null;
  status?: string;
  note?: string;
};

export const fetchProcurements = (filters: { state?: "open" | "closed" } = {}) =>
  json<ProcurementRow[]>(`/api/procurements/?${queryString(filters)}`);
export const fetchProcurement = (id: number) => json<Procurement>(`/api/procurements/${id}/`);
export const createProcurement = (payload: ProcurementPayload) => json<Procurement>("/api/procurements/", post(payload));
export const updateProcurement = (id: number, payload: ProcurementPayload) =>
  json<Procurement>(`/api/procurements/${id}/`, patch(payload));
export const createPass = (procurementId: number, payload: PassPayload) =>
  json<ProcurementPass>(`/api/procurements/${procurementId}/passes/`, post(payload));
export const updatePass = (id: number, payload: PassPayload) => json<ProcurementPass>(`/api/passes/${id}/`, patch(payload));
export const deletePass = (id: number) => json<void>(`/api/passes/${id}/`, { method: "DELETE" });

export async function uploadProcurementDocument(procurementId: number, file: File, kind: string): Promise<ProcurementDocument> {
  const body = new FormData();
  body.append("file", file);
  body.append("kind", kind);
  return json<ProcurementDocument>(`/api/procurements/${procurementId}/documents/`, { method: "POST", body });
}

export const deleteProcurementDocument = (id: number) =>
  json<void>(`/api/procurement-documents/${id}/`, { method: "DELETE" });

/** Файл закрыт токеном — качаем через blob, как журнал. */
export async function downloadProcurementDocument(doc: ProcurementDocument): Promise<void> {
  const response = await call(`/api/procurement-documents/${doc.id}/`);
  if (!response.ok) throw new ApiError(`Не удалось скачать файл (${response.status})`, response.status);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = doc.filename || doc.title;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

// ---------------------------------------------------------------------------
// Заказы — вкладка «Поверки» (apps/verification/api_orders.py)
// ---------------------------------------------------------------------------
export type PartyLookup =
  | { found: false; valid: boolean }
  | { found: true; inn: string; name: string; kpp: string; ogrn: string; address: string; phone: string;
      client_id: number | null; source: "own" | "dadata"; status: string };

export const lookupParty = (inn: string) => json<PartyLookup>(`/api/clients/lookup/?inn=${encodeURIComponent(inn)}`);

export type OrderPayload = {
  inn?: string;
  name?: string;
  kpp?: string;
  ogrn?: string;
  address?: string;
  phone?: string;
  contact?: string;
  note?: string;
  procurement_id?: number | null;
  assigned_employee_id?: number;
};

export const createOrder = (payload: OrderPayload) =>
  json<WorkOrder>("/api/orders/", { method: "POST", body: JSON.stringify(payload) });
export const updateOrder = (id: number, payload: OrderPayload) =>
  json<WorkOrder>(`/api/orders/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
export const issueOrder = (id: number, issued: boolean) =>
  json<WorkOrder>(`/api/orders/${id}/issue/`, { method: "POST", body: JSON.stringify({ issued }) });

export async function downloadOrderAct(id: number, kind: "receive" | "issue"): Promise<void> {
  const response = await call(`/api/orders/${id}/act/${kind}.pdf`);
  if (!response.ok) {
    let detail = `Не удалось сформировать акт (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* не JSON */
    }
    throw new ApiError(detail, response.status);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : `Акт ${kind === "receive" ? "приёма" : "выдачи"} ${id}.pdf`;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

// ---------------------------------------------------------------------------
// Запись на поверку из сервиса (apps/logistics/api.py: VisitCreateView)
// ---------------------------------------------------------------------------
export type VisitDefaults = { work_day: [string, string]; city: string; can_assign: boolean };
export type VisitPayload = {
  contact_name?: string;
  contact_phone?: string;
  address: string;
  postal_code?: string;
  fias_id?: string;
  district?: string;
  latitude?: number | null;
  longitude?: number | null;
  date: string;
  window_start?: string | null;
  window_end?: string | null;
  employee_id?: number | null;
  quantity?: number | null;
  comment?: string;
  source?: "phone" | "manual" | "email";
};
export type VisitResult = {
  request_id: number;
  work_order_id: number;
  trip_id: number;
  stop_id: number;
  employee: string;
  trip_title: string;
};

export const fetchVisitDefaults = () => json<VisitDefaults>("/api/visits/");
export const createVisit = (payload: VisitPayload) =>
  json<VisitResult>("/api/visits/", { method: "POST", body: JSON.stringify(payload) });
