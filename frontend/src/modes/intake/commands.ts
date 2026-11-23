/** Палитра команд (⌘K, «/»): приёмки по заказчику, ИНН, номеру приёмки и заводскому номеру прибора.
 *  Регистрирует оболочка (modes/commandSources.ts) — работает из любого режима. */

import { fetchOwnHistory } from "../../api";
import { registerCommandSource, scoreText, type CommandItem } from "../../ui/CommandPalette";
import type { Go } from "../types";
import { STAGES, fetchIntake, stageOf, type IntakeOrder } from "./api";

let cache: { at: number; data: Promise<IntakeOrder[]> } | null = null;
function openIntake(): Promise<IntakeOrder[]> {
  if (!cache || Date.now() - cache.at > 60_000) {
    const data = fetchIntake({ issued: "0" });
    cache = { at: Date.now(), data };
    data.catch(() => (cache = null));
  }
  return cache.data;
}

const stageTitle = (o: IntakeOrder) => STAGES.find((s) => s.key === stageOf(o))?.title ?? "";

export function registerIntakeSources(go: Go): () => void {
  const open = (id: number) => go("intake", { sub: String(id), action: "open-order", payload: id });
  const offOrders = registerCommandSource(
    "intake",
    async (query): Promise<CommandItem[]> => {
      const list = await openIntake();
      const q = query.replace(/^№\s*/, "").trim();
      const byId = /^\d{1,7}$/.test(q) ? list.filter((o) => String(o.id) === q || o.client_inn.startsWith(q)) : [];
      const byText = list
        .map((o) => ({ o, score: scoreText(`${o.client} ${o.client_inn} ${o.contact} ${o.procurement_label}`, q) }))
        .filter((row) => row.score > 0)
        .sort((a, b) => b.score - a.score)
        .map((row) => row.o);
      const seen = new Set<number>();
      return [...byId, ...byText]
        .filter((o) => !seen.has(o.id) && seen.add(o.id))
        .slice(0, 6)
        .map((o) => ({
          id: `intake:${o.id}`,
          title: `Приёмка № ${o.id} · ${o.client}`,
          subtitle: [stageTitle(o), o.client_inn && `ИНН ${o.client_inn}`, o.verifications_total ? `${o.verifications_total} шт.` : "", o.assigned_employee].filter(Boolean).join(" · "),
          icon: "box" as const,
          run: () => open(o.id),
        }));
    },
    { group: "Приёмки", order: 25 },
  );
  const offSerial = registerCommandSource(
    "intake-serial",
    async (query, signal): Promise<CommandItem[]> => {
      const serial = query.trim();
      if (!/\d/.test(serial) || signal.aborted) return [];
      const { own } = await fetchOwnHistory(serial);
      return own
        .filter((row) => row.last?.work_order_id)
        .slice(0, 4)
        .map((row) => ({
          id: `intake-serial:${row.instrument_id}`,
          title: `${row.si_type.label} · зав. № ${row.serial_number}`,
          subtitle: [row.owner, row.last?.status, row.last?.verified_at && new Date(row.last.verified_at).toLocaleDateString("ru-RU")].filter(Boolean).join(" · "),
          icon: "barcode" as const,
          mono: true,
          run: () => open(row.last!.work_order_id!),
        }));
    },
    { group: "Приёмки · заводской номер", minQuery: 4, order: 26 },
  );
  return () => {
    offOrders();
    offSerial();
  };
}
