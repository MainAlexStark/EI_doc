/** API режима «Приёмки» (apps/verification/api_orders.py, api_field.py, apps/logistics/api.py). */

import { fetchWorkOrders, json, queryString, type WorkOrder } from "../../api";

/** Отметка выезда, с которым связана приёмка: забрали у заказчика / везём обратно. */
export type LogisticMark = {
  stop_id: number;
  trip_id: number;
  trip_title: string;
  status: string;
  planned_date: string | null;
};
export type IntakeOrder = WorkOrder & { pickup?: LogisticMark | null; delivery?: LogisticMark | null };

export type Stage = "receive" | "verify" | "ready" | "issued";
export const STAGES: { key: Stage; title: string; tone: string }[] = [
  { key: "receive", title: "Приём", tone: "info" },
  { key: "verify", title: "Поверка", tone: "warn" },
  { key: "ready", title: "Готов к выдаче", tone: "ok" },
  { key: "issued", title: "Выдан", tone: "muted" },
];

/** Стадия считается из поверок: нет приборов — приём, не всё поверено — поверка, иначе готов. */
export function stageOf(o: WorkOrder): Stage {
  if (o.issued_at) return "issued";
  if (o.verifications_total === 0) return "receive";
  if (o.verifications_ready < o.verifications_total) return "verify";
  return "ready";
}

export function fetchIntake(params: { issued: "0" | "1"; employee?: number | null; limit?: number }): Promise<IntakeOrder[]> {
  const filters: Record<string, string> = { kind: "lab", trip: "none", issued: params.issued };
  if (params.employee) filters.assigned_employee = String(params.employee);
  if (params.limit) filters.limit = String(params.limit);
  return json<IntakeOrder[]>(`/api/work-orders/?${queryString(filters)}`);
}

export const fetchIntakeOne = (id: number) =>
  fetchWorkOrders({ id: String(id) }).then((list) => (list[0] as IntakeOrder | undefined) ?? null);

export type BulkRow = { si_type_id: number; serial_number: string; manufacture_year?: number | null; client_id?: string };
export type BulkVerification = {
  id: number;
  client_id: string;
  work_order: number;
  si_type_id: number;
  instrument: string;
  serial_number: string;
  status: string;
  status_display: string;
};
export type BulkResult = {
  created: BulkVerification[];
  existing: BulkVerification[];
  errors: { index: number; serial_number: string; error: string }[];
};

export const bulkInstruments = (orderId: number, rows: BulkRow[]) =>
  json<BulkResult>(`/api/orders/${orderId}/instruments/bulk/`, { method: "POST", body: JSON.stringify({ rows }) });

export type DeliveryPayload = { date: string; employee_id?: number | null; window_start?: string | null; window_end?: string | null; note?: string };
export type DeliveryResult = { id: number; trip_id: number; trip_title: string; employee: string };

export const createDelivery = (orderId: number, payload: DeliveryPayload) =>
  json<DeliveryResult>(`/api/orders/${orderId}/delivery/`, { method: "POST", body: JSON.stringify(payload) });
