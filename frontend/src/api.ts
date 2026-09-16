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
  suitable: boolean;
  status: string;
  needs_review: boolean;
  journal_note: string;
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

async function call(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
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

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await call(path, init);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
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
  page?: number;
};

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
export async function downloadJournal(filters: JournalFilters): Promise<void> {
  const response = await call(`/api/journal/export/?${queryString(filters)}`);
  if (!response.ok) throw new ApiError(`Не удалось выгрузить журнал (${response.status})`);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "Журнал учёта поверочных работ.xlsx";
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
};

export type WorkOrderFilters = { status?: string; assigned_employee?: string; date_from?: string; date_to?: string };

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

export const fetchMyAvailability = (filters: { date_from?: string; date_to?: string } = {}) =>
  json<Page<AvailabilitySlot>>(`/api/hub/availability/?${queryString(filters)}`);

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
};

export type SiTypeSuggestResponse = { results: SiTypeSuggestion[]; warning?: string };

/** Подсказка типов СИ — авторизованный эндпоинт, поэтому через json(), не суём в public-хелперы. */
export async function suggestSiTypes(query: string): Promise<SiTypeSuggestResponse> {
  if (query.trim().length < 2) return { results: [] };
  try {
    return await json<SiTypeSuggestResponse>(`/api/catalog/si-types/suggest/?${queryString({ q: query })}`);
  } catch {
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

export const submitMeasurements = (verificationId: number, payload: MeasurementsPayload) =>
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
