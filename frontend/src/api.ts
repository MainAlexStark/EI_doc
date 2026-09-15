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

export class ApiError extends Error {}

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
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

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
    throw new ApiError(detail);
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
  si_description?: string;
  desired_date?: string | null;
  comment?: string;
  website?: string; // honeypot — держать пустым
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
  si_description: string;
  desired_date: string | null;
  comment: string;
  suggested_employee: string;
  suggested_employee_id: number | null;
  assigned_employee: string;
  assigned_employee_id: number | null;
  reject_reason: string;
  is_address_confirmed: boolean;
  created_at: string;
};

export type RequestFilters = { status?: string; district?: string; assigned_employee?: string };

export const fetchRequests = (filters: RequestFilters = {}) =>
  json<HubRequest[]>(`/api/hub/requests/list/?${queryString(filters)}`);

export const routeRequest = (id: number) =>
  json<HubRequest>(`/api/hub/requests/${id}/route/`, { method: "POST" });

export const confirmRequest = (
  id: number,
  payload: { employee_id: number; scheduled_date?: string | null; note?: string },
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

