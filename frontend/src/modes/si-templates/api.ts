/** API режима «Шаблоны поверки СИ»: рабочие места (чат на тип СИ — assistant/api.ts), шаблоны поверки
 *  (общий api.ts) и то, как из них складывается статус «черновик / на проверке / проверен». */

import { fetchVerificationTemplates, type VerificationTemplate } from "../../api";
import type { TemplateDigest, TemplateWorkspace } from "../../assistant/api";

export type TplStatus = "draft" | "review" | "reviewed";

export const STATUS_LABEL: Record<TplStatus, string> = {
  draft: "черновик",
  review: "на проверке",
  reviewed: "проверен",
};
export const STATUS_PILL: Record<TplStatus, string> = { draft: "muted", review: "warn", reviewed: "ok" };

/** Статус рабочего места: есть неопубликованный черновик → «черновик»; опубликован — по действующему шаблону типа. */
export function workspaceStatus(ws: TemplateWorkspace, active?: VerificationTemplate | TemplateDigest | null): TplStatus {
  if (ws.has_draft || ws.status !== "published") return "draft";
  const published = active ?? ws.published ?? null;
  if (!published) return "draft";
  return published.needs_review ? "review" : "reviewed";
}

export const templateStatus = (t: Pick<VerificationTemplate, "needs_review">): TplStatus => (t.needs_review ? "review" : "reviewed");

/** Действующие шаблоны по id типа СИ (для статуса в списке рабочих мест — там нет дайджеста). */
export function activeBySiType(templates: VerificationTemplate[]): Map<number, VerificationTemplate> {
  const map = new Map<number, VerificationTemplate>();
  for (const t of templates) {
    if (!t.is_active || t.si_type == null) continue;
    const prev = map.get(t.si_type);
    if (!prev || prev.updated_at < t.updated_at) map.set(t.si_type, t);
  }
  return map;
}

/** Короткий кэш всех шаблонов: список, счётчик «на проверке» и палитра команд спрашивают одно и то же. */
let cache: { at: number; data: Promise<VerificationTemplate[]> } | null = null;
export function allTemplates(fresh = false): Promise<VerificationTemplate[]> {
  if (fresh || !cache || Date.now() - cache.at > 30_000) {
    const data = fetchVerificationTemplates(true);
    cache = { at: Date.now(), data };
    data.catch(() => (cache = null));
  }
  return cache.data;
}
export const dropTemplatesCache = () => {
  cache = null;
};

/** Сколько шаблонов поверки ждёт метролога (счётчик в боковой панели). */
export const countTemplatesForReview = () =>
  allTemplates(true).then((list) => list.filter((t) => t.is_active && t.needs_review).length);

// --- Что изменилось между редакциями черновика ----------------------------
export type DigestKey = "method" | "si_kind" | "params" | "operations" | "modes" | "points" | "characteristics" | "kits" | "sources" | "check";
export type DigestDiff = { changed: Set<DigestKey>; added: Map<DigestKey, Set<string>>; removed: Map<DigestKey, string[]> };

const listOf = (d: TemplateDigest, key: DigestKey): string[] => {
  switch (key) {
    case "params":
      return d.params.map((p) => `${p.label}${p.unit ? `, ${p.unit}` : ""} — ${p.from_type ? "из типа" : "вносит поверитель"}`);
    case "points":
      return d.points.map((p) => (d.repeats && d.repeats > 1 ? `${p} × ${d.repeats}` : p));
    case "operations":
    case "modes":
    case "characteristics":
    case "kits":
    case "sources":
      return d[key];
    case "check":
      return [d.check == null ? "—" : d.check.ok ? "ok" : "fail"];
    default:
      return [String(d[key] ?? "")];
  }
};

export const DIGEST_KEYS: DigestKey[] = ["method", "si_kind", "params", "operations", "modes", "points", "characteristics", "kits", "sources", "check"];

export function diffDigest(prev: TemplateDigest | null, next: TemplateDigest | null): DigestDiff | null {
  if (!prev || !next) return null;
  const diff: DigestDiff = { changed: new Set(), added: new Map(), removed: new Map() };
  for (const key of DIGEST_KEYS) {
    const a = listOf(prev, key);
    const b = listOf(next, key);
    if (a.join("\u0001") === b.join("\u0001")) continue;
    diff.changed.add(key);
    const before = new Set(a);
    const after = new Set(b);
    diff.added.set(key, new Set(b.filter((x) => !before.has(x))));
    diff.removed.set(key, a.filter((x) => !after.has(x)));
  }
  return diff.changed.size ? diff : null;
}

export const DIGEST_LABEL: Record<DigestKey, string> = {
  method: "Методика",
  si_kind: "Вид СИ",
  params: "Параметры прибора",
  operations: "Операции поверки",
  modes: "Поверяемые точки",
  points: "Величины в точке",
  characteristics: "Характеристики",
  kits: "Наборы эталонов",
  sources: "Источники",
  check: "Сверка с примером",
};
