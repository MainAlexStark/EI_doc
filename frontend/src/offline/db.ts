/**
 * Локальный кэш для полевой работы без связи (IndexedDB через Dexie).
 *
 * Поверки живут здесь по client_id — тому же полю, что и в
 * Verification.client_id на бэкенде (см. apps/verification/models.py):
 * до синхронизации у записи ещё нет серверного id, поэтому UI не может
 * ключеваться по нему. server_id появляется, когда create_verification
 * из очереди (outbox) успешно проходит.
 */
import Dexie, { type Table } from "dexie";

export type CachedWorkOrder = {
  id: number;
  status: string;
  status_display: string;
  client: string;
  site: string;
  assigned_employee: string;
  assigned_employee_id: number;
  scheduled_date: string | null;
  scheduled_time: string | null;
  cached_at: number;
};

export type SyncState = "pending" | "synced";

export type CachedVerification = {
  client_id: string;
  work_order_id: number;
  server_id: number | null;
  si_type_id: number;
  si_type_name: string;
  serial_number: string;
  manufacture_year: number | null;
  verified_at: string;
  status: string;
  status_display: string;
  suitable: boolean;
  needs_review: boolean;
  sync_state: SyncState;
};

export type OutboxKind = "create_verification" | "submit_measurements";

export type OutboxItem = {
  id: string;
  kind: OutboxKind;
  work_order_id?: number;
  verification_client_id: string;
  payload: unknown;
  created_at: number;
  attempts: number;
  last_error?: string;
};

export type MetaEntry = { key: string; data: unknown; cached_at: number };

class OfflineDB extends Dexie {
  workOrders!: Table<CachedWorkOrder, number>;
  verifications!: Table<CachedVerification, string>;
  outbox!: Table<OutboxItem, string>;
  meta!: Table<MetaEntry, string>;

  constructor() {
    super("ei_doc_offline");
    this.version(1).stores({
      workOrders: "id, assigned_employee_id, status",
      verifications: "client_id, work_order_id, server_id, sync_state",
      outbox: "id, kind, created_at",
      meta: "key",
    });
  }
}

export const db = new OfflineDB();
