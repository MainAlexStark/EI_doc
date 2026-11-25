/** Межповерочный интервал и дата следующей поверки — то, ради чего тип СИ чаще всего ищут:
 * у клиента выписывают бумагу и пишут, до какого числа действует поверка.
 *
 * Поиск ФГИС МПИ не отдаёт — он есть только в карточке типа. Сервер подставляет его из уже
 * скачанных карточек, остальное экран дозапрашивает (`useFifMpi`) по одной карточке на тип,
 * по две одновременно — ФГИС держит лимит запросов. Ответы помнятся до перезагрузки страницы. */

import { useEffect, useState } from "react";
import { json } from "./api";

export type MpiRow = { parameter: string; months: number };

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** 72 → «6 лет», 48 → «4 года», 18 → «18 мес.». */
export function formatInterval(months: number): string {
  if (months % 12 === 0) {
    const years = months / 12;
    return `${years} ${plural(years, "год", "года", "лет")}`;
  }
  return `${months} мес.`;
}

/** Поверка действует до даты поверки + МПИ минус один день (как в свидетельстве и в Аршин). */
export function nextVerification(months: number, from: Date = new Date()): Date {
  const day = from.getDate();
  const target = new Date(from.getFullYear(), from.getMonth() + months, 1);
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  target.setDate(Math.min(day, lastDay));
  target.setDate(target.getDate() - 1);
  return target;
}

export function ruDate(value: Date): string {
  return value.toLocaleDateString("ru-RU");
}

/** «хол. вода» вместо длинной формулировки реестра — в строке поиска мало места. */
export function shortParameter(text: string): string {
  const t = text.trim();
  if (!t) return "";
  const low = t.toLowerCase();
  if (low.includes("холодн")) return "хол.";
  if (low.includes("горяч")) return "гор.";
  return t.length > 28 ? `${t.slice(0, 26)}…` : t;
}

// ---------------------------------------------------------------------------
// Дозапрос МПИ из карточек реестра
// ---------------------------------------------------------------------------
type Entry = MpiRow[] | "failed";
const known = new Map<string, Entry>();
const waiting: string[] = [];
const listeners = new Set<() => void>();
let running = 0;
const PARALLEL = 2;

function pump(): void {
  while (running < PARALLEL && waiting.length) {
    const uuid = waiting.shift()!;
    if (known.has(uuid)) continue;
    running += 1;
    json<{ mpi: MpiRow[] }>(`/api/catalog/fif/types/${encodeURIComponent(uuid)}/mpi/`)
      .then((data) => known.set(uuid, data.mpi ?? []))
      .catch(() => known.set(uuid, "failed"))
      .finally(() => {
        running -= 1;
        listeners.forEach((fn) => fn());
        pump();
      });
  }
}

/** Запомнить МПИ, который уже пришёл в ответе поиска (из кэша сервера). */
export function rememberMpi(uuid: string | undefined, mpi: MpiRow[] | null | undefined): void {
  if (uuid && mpi && !known.has(uuid)) known.set(uuid, mpi);
}

/** МПИ по mit_uuid: уже известный — сразу, остальные — дозапрос в очереди. */
export function useFifMpi(uuids: string[]): Record<string, MpiRow[] | "loading" | "failed"> {
  const [, setTick] = useState(0);
  const key = uuids.join("|");
  useEffect(() => {
    const fn = () => setTick((n) => n + 1);
    listeners.add(fn);
    let added = false;
    for (const uuid of uuids) {
      if (uuid && !known.has(uuid) && !waiting.includes(uuid)) {
        waiting.push(uuid);
        added = true;
      }
    }
    if (added) pump();
    return () => {
      listeners.delete(fn);
      // Ушли со списка — не тратим лимит ФГИС на то, что уже не на экране.
      for (const uuid of uuids) {
        const at = waiting.indexOf(uuid);
        if (at >= 0) waiting.splice(at, 1);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const out: Record<string, MpiRow[] | "loading" | "failed"> = {};
  for (const uuid of uuids) out[uuid] = known.get(uuid) ?? "loading";
  return out;
}
