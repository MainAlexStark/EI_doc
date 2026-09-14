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

export function queryString(filters: JournalFilters): string {
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
