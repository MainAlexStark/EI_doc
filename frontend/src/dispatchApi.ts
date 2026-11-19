/** API диспетчерской руководства и сотрудников (apps/logistics/api_dispatch.py). */

import { json, queryString } from "./api";

export const CONTROL_ROLES = new Set(["owner", "head", "metrologist", "manager"]);
export const ADMIN_ROLES = new Set(["owner", "head"]);
export const isControlRole = (role: string | undefined) => !!role && CONTROL_ROLES.has(role);

export type AssignType = "stop" | "order" | "request";
export type AssignItem = { type: AssignType; id: number };
export type AssignResult = {
  done: { type: AssignType; id: number; text: string; trip_id: number | null }[];
  errors: { type: AssignType; id: number; error: string }[];
};

export type SlotBrief = { kind: "district" | "trip"; start: string | null; end: string | null; is_priority: boolean };
export type TripRef = { id: number; title: string; kind: string; destination?: string; date_start?: string | null; date_end?: string | null; is_lead: boolean };

export type TeamCard = {
  id: number;
  full_name: string;
  short_name: string;
  position: string;
  role: string;
  state: "trip" | "city" | "free" | "off";
  city_trip_id: number | null;
  stops: { total: number; done: number; waiting: number; missed: number };
  trips: TripRef[];
  slots: SlotBrief[];
  lab_open: number;
  drafts: number;
  ready: number;
  rejected: number;
  tasks_open: number;
  tasks_overdue: number;
  week_stops: number;
};

export type Attention = {
  type: "request" | "order" | "issue" | "task";
  id: number;
  title: string;
  subtitle: string;
  date: string | null;
  time: string | null;
  suggested_employee_id?: number | null;
  suggested_employee?: string;
};

export type Overview = {
  date: string;
  totals: {
    stops_total: number; stops_done: number; stops_waiting: number;
    on_trip: number; in_city: number; free: number;
    requests_new: number; unscheduled: number; lab_open: number; lab_ready: number;
    review: number; rejected: number; tasks_overdue: number; unlocated: number;
  };
  attention: Attention[];
  employees: TeamCard[];
};

export type MapPoint = {
  type: AssignType;
  id: number;
  address: string;
  client: string;
  contact_name?: string;
  contact_phone: string;
  date: string | null;
  window_start: string | null;
  window_end: string | null;
  planned_count: number | null;
  employee_id: number | null;
  employee: string;
  latitude: number | null;
  longitude: number | null;
  note: string;
  // только у адресов выездов
  trip_id?: number;
  trip_title?: string;
  trip_kind?: "city" | "classic" | "procurement";
  position?: number;
  status?: "planned" | "done" | "no_answer" | "postponed" | "refused";
  status_display?: string;
  work_order_id?: number | null;
  // только в очереди
  source?: string;
};

export type DispatchBoard = {
  date_from: string;
  date_to: string;
  points: MapPoint[];
  unlocated: MapPoint[];
  queue: MapPoint[];
  trips: { id: number; title: string; kind: string; lead_id: number; lead: string; date_start: string | null; date_end: string | null; status: string; member_ids: number[] }[];
  employees: { id: number; full_name: string; short_name: string }[];
};

export type ScheduleCell = {
  slots: { id: number; kind: "district" | "trip"; start: string | null; end: string | null; is_priority: boolean; note: string }[];
  stops: number;
  done: number;
  trips: { id: number; title: string; kind: string; is_lead: boolean }[];
  tasks: number;
  lab: number;
};

export type TeamSchedule = {
  date_from: string;
  date_to: string;
  days: string[];
  employees: { id: number; full_name: string; short_name: string; position: string; cells: Record<string, ScheduleCell> }[];
};

export const fetchOverview = (date?: string) => json<Overview>(`/api/dispatch/overview/?${queryString({ date })}`);
export const fetchDispatchBoard = (date_from: string, date_to: string) =>
  json<DispatchBoard>(`/api/dispatch/map/?${queryString({ date_from, date_to })}`);
export const fetchTeamSchedule = (date_from: string, date_to: string) =>
  json<TeamSchedule>(`/api/dispatch/schedule/?${queryString({ date_from, date_to })}`);
export const assignWork = (payload: { items: AssignItem[]; employee_id: number; date?: string | null; time?: string | null }) =>
  json<AssignResult>("/api/dispatch/assign/", { method: "POST", body: JSON.stringify(payload) });

// ---------------------------------------------------------------------------
// Сотрудники
// ---------------------------------------------------------------------------
export type StaffRow = {
  id: number;
  full_name: string;
  short_name: string;
  tab_number: string;
  position: string;
  is_active: boolean;
  email: string;
  role: string;
  role_display: string;
  has_login: boolean;
  last_login: string | null;
  telegram_linked: boolean;
  attestation_valid_to: string | null;
  attestation_nearest: string | null;
  attestation_ok: boolean;
};

export type StaffList = {
  can_admin: boolean;
  roles: { value: string; label: string }[];
  next_tab_number: string;
  staff: StaffRow[];
};

export type StaffPayload = Partial<{
  full_name: string; short_name: string; position: string; tab_number: string; email: string; role: string; is_active: boolean;
}>;

export const fetchStaff = (all = false) => json<StaffList>(`/api/core/staff/${all ? "?all=1" : ""}`);
export const createStaff = (payload: StaffPayload) =>
  json<{ employee: StaffRow; password: string }>("/api/core/staff/", { method: "POST", body: JSON.stringify(payload) });
export const updateStaff = (id: number, payload: StaffPayload) =>
  json<{ employee: StaffRow; password: string }>(`/api/core/staff/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
export const resetStaffPassword = (id: number) =>
  json<{ password: string; email: string }>(`/api/core/staff/${id}/password/`, { method: "POST" });

// ---------------------------------------------------------------------------
// Цвета сотрудников — те же, что у поверителей в общем журнале: по порядку id,
// поэтому у человека цвет один и тот же на карте, в графике и на панели.
// ---------------------------------------------------------------------------
export const TEAM_COLORS = [
  "#0a84ff", "#ff9f0a", "#30b158", "#af52de", "#30b0c7",
  "#5856d6", "#a2845e", "#d4a800", "#00a89f", "#ff6fb1",
];

export function teamColors(ids: number[]): Map<number, string> {
  const map = new Map<number, string>();
  [...ids].sort((a, b) => a - b).forEach((id, index) => map.set(id, TEAM_COLORS[index % TEAM_COLORS.length]));
  return map;
}

export const isoDay = (date: Date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

export const addDays = (iso: string, days: number) => {
  const date = new Date(`${iso}T12:00:00`);
  date.setDate(date.getDate() + days);
  return isoDay(date);
};

export const dayLabel = (iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", opts).replace(/\./g, "");

export const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
