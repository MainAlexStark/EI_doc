/** API режима «Шаблоны аттестации» — шаблоны планов аттестации ИО по виду оборудования
 *  (apps/equipment/api.py: /api/attestations/programs/…) и типы плана (docs/attestation.md). */

import { json } from "../../api";

export type Point = { key: string; label: string; role?: "volume" | "control"; groups?: string[] };
export type Mode = { key: string; label: string; setpoint: string; params?: Record<string, string> };
export type Tolerance = { kind: "range" | "abs" | "none"; min?: string; max?: string; limit?: string };
export type Characteristic = {
  key: string; expr: string; text?: string; per?: "mode" | "point"; group?: string; label: string; decimals?: number;
  tolerance?: Tolerance; source?: string; preset?: string;
};
export type Condition = { key: string; label: string; unit?: string; decimals?: number };
export type PlanCheckRow = { mode: string; key: string; label: string; point: string; expected: string; computed: string; ok: boolean };
export type PlanCheck = {
  source?: string; ok?: boolean; stale?: boolean; errors?: string[]; unchecked?: string[]; rows?: PlanCheckRow[];
  checked_at?: string; checked_by?: string;
};
/** Числа примера протокола для сверки (calc.check_example) — на сервере не сохраняются. */
export type CheckExample = {
  source: string;
  modes: Record<string, { display?: string; readings: Record<string, string[]>; expected: Record<string, string | Record<string, string>> }>;
};
export type Plan = {
  equipment_kind: string; program: string; documents: string[]; quantity: string; unit: string;
  checked?: { label: string; value: string }[]; repeats: number; points: Point[]; modes: Mode[];
  characteristics: Characteristic[]; conditions: Condition[]; procedure: string[]; purpose: string;
  periodicity: string; layout?: unknown[];
  [extra: string]: unknown;
};

export type Program = {
  id: number;
  title: string;
  equipment_kind: string;
  note: string;
  needs_review: boolean;
  status: "draft" | "reviewed";
  reviewed_by: string;
  reviewed_at: string | null;
  created_by: string;
  created_via_assistant: boolean;
  source_attestation_id: number | null;
  is_active: boolean;
  used: number | null;
  sources: string[];
  plan?: Plan;
  /** Отчёт сверки формул с примером; null — сверки не было. */
  plan_check?: PlanCheck | null;
};

export type AttestationBrief = {
  id: number; number: string; status: "draft" | "signed"; kind: string; name: string; serial_number: string;
  owner: string; performed_on: string | null; protocol_date: string | null; suitable: boolean | null; plan_approved: boolean;
};

export type EquipmentCard = Record<"name" | "serial_number" | "inventory_number" | "manufacturer" | "year" | "owner" | "location", string>;

const P = "/api/attestations/programs/";

export const fetchPrograms = (opts: { all?: boolean; status?: "draft" | "reviewed"; kind?: string; plan?: boolean } = {}) => {
  const q = new URLSearchParams();
  if (opts.all) q.set("all", "1");
  if (opts.status) q.set("status", opts.status);
  if (opts.kind) q.set("equipment_kind", opts.kind);
  if (opts.plan === false) q.set("plan", "0");
  const s = q.toString();
  return json<Program[]>(`${P}${s ? `?${s}` : ""}`);
};
export const fetchProgram = (id: number) => json<Program>(`${P}${id}/`);
export const updateProgram = (id: number, patch: Partial<Pick<Program, "title" | "equipment_kind" | "note" | "is_active">> & { plan?: Plan }) =>
  json<Program>(`${P}${id}/`, { method: "PATCH", body: JSON.stringify(patch) });
export const createProgram = (body: { title: string; equipment_kind: string; plan: Plan; note?: string }) =>
  json<Program>(P, { method: "POST", body: JSON.stringify(body) });
export const checkProgram = (id: number, example: CheckExample) =>
  json<Program>(`${P}${id}/check/`, { method: "POST", body: JSON.stringify({ example }) });
export const approveProgram = (id: number) => json<Program>(`${P}${id}/approve/`, { method: "POST" });
export const applyProgram = (id: number, body: { equipment: Partial<EquipmentCard>; kind?: string }) =>
  json<{ id: number }>(`${P}${id}/apply/`, { method: "POST", body: JSON.stringify(body) });
export const programFromAttestation = (attestationId: number, body: { title?: string; equipment_kind?: string; note?: string } = {}) =>
  json<Program>(`/api/attestations/${attestationId}/save-as-program/`, { method: "POST", body: JSON.stringify(body) });
export const fetchAttestationList = (q = "") => json<AttestationBrief[]>(`/api/attestations/?q=${encodeURIComponent(q.trim())}`);

/** Счётчик «на проверке» для боковой панели. */
export const countProgramsForReview = () => fetchPrograms({ status: "draft", plan: false }).then((list) => list.length);

export const EQUIPMENT_KINDS = [
  "Холодильник фармацевтический",
  "Термостат",
  "Шкаф сушильный",
  "Камера климатическая",
  "Печь муфельная",
  "Баня водяная",
  "Морозильник",
  "Инкубатор",
];

export const KIND_LABEL = { primary: "первичная", periodic: "периодическая", repeat: "повторная" } as const;

export const EQUIPMENT_FIELDS: [keyof EquipmentCard, string][] = [
  ["name", "Наименование"], ["serial_number", "Заводской номер"], ["inventory_number", "Инвентарный номер"],
  ["manufacturer", "Изготовитель"], ["year", "Год выпуска"], ["owner", "Владелец"], ["location", "Место установки"],
];

export const comma = (v: string | number | undefined | null) => String(v ?? "").replace(".", ",");

/** Допуск словами — как в протоколе (без расчёта выражений: их считает сервер). */
export function toleranceText(t: Tolerance | undefined): string {
  if (!t || t.kind === "none") return "без допуска";
  if (t.kind === "abs") return `±${comma(t.limit)}`;
  if (t.min && t.max) return `от ${comma(t.min)} до ${comma(t.max)}`;
  if (t.min) return `не менее ${comma(t.min)}`;
  if (t.max) return `не более ${comma(t.max)}`;
  return "—";
}

/** Вид ИО для группировки: «Термостат ТС-1/80» и «термостат» — один вид. */
export const kindKey = (kind: string) => kind.trim().toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ");
