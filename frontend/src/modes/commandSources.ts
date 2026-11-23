/** Поиск по сущностям в палитре команд (⌘K): заявки, заводские номера и протоколы журнала,
 *  закупки, командировки. Регистрирует оболочка после входа; режимы могут добавить свои
 *  через registerCommandSource (ui/CommandPalette.tsx). */

import { fetchJournal, fetchProcurements, fetchRequests, fetchTrips, type HubRequest, type ProcurementRow, type TripBrief } from "../api";
import { registerCommandSource, scoreText, type CommandItem } from "../ui/CommandPalette";
import type { Access, Go } from "./types";
import { registerIntakeSources } from "./intake/commands";

/** Короткий кэш списков — палитра ищет на каждом нажатии, а списки меняются редко. */
function cached<T>(load: () => Promise<T>, ttl = 60_000): () => Promise<T> {
  let value: { at: number; data: Promise<T> } | null = null;
  return () => {
    if (!value || Date.now() - value.at > ttl) {
      const data = load();
      value = { at: Date.now(), data };
      data.catch(() => (value = null));
    }
    return value.data;
  };
}

const digits = (text: string) => text.replace(/\D/g, "");
const ruDate = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";

function top<T>(list: T[], query: string, text: (item: T) => string, limit = 6): T[] {
  return list
    .map((item) => ({ item, score: scoreText(text(item), query) }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((row) => row.item);
}

export function registerDefaultSources(go: Go, access: Access): () => void {
  const offs: (() => void)[] = [];

  if (access.isManager) {
    const requests = cached(() => fetchRequests({}));
    offs.push(
      registerCommandSource(
        "requests",
        async (query): Promise<CommandItem[]> => {
          const list = await requests();
          const q = query.replace(/^№\s*/, "");
          const qDigits = digits(q);
          const byId = /^\d{1,6}$/.test(q) ? list.filter((r) => String(r.id) === q) : [];
          const byPhone = qDigits.length >= 4 ? list.filter((r) => digits(r.contact_phone).includes(qDigits)) : [];
          const byText = top(list, q, (r: HubRequest) => `${r.contact_name} ${r.address} ${r.district} ${r.si_description} ${r.contact_email}`);
          const seen = new Set<number>();
          return [...byId, ...byPhone, ...byText]
            .filter((r) => !seen.has(r.id) && seen.add(r.id))
            .map((r) => ({
              id: `request:${r.id}`,
              title: `Заявка №${r.id} · ${r.contact_name || "без имени"}`,
              subtitle: [r.status_display, r.address, r.contact_phone].filter(Boolean).join(" · "),
              icon: "inbox" as const,
              run: () => go("requests", { action: "open", payload: r.id }),
            }));
        },
        { group: "Заявки", order: 10 },
      ),
    );
  }

  offs.push(
    registerCommandSource(
      "journal",
      async (query, signal): Promise<CommandItem[]> => {
        if (signal.aborted) return [];
        const page = await fetchJournal({ q: query, page: 1 });
        return page.results.slice(0, 6).map((row) => ({
          id: `journal:${row.id}`,
          title: row.si_name || "Протокол",
          subtitle: [`зав. № ${row.serial_number}`, row.protocol_number && `протокол ${row.protocol_number}`, ruDate(row.verified_at), row.owner]
            .filter(Boolean)
            .join(" · "),
          mono: true,
          icon: row.suitable ? ("checkCircle" as const) : ("ban" as const),
          run: () => go("journal", { action: "search", payload: row.serial_number || query }),
        }));
      },
      { group: "Журнал · заводские номера и протоколы", minQuery: 3, order: 20 },
    ),
  );

  const procurements = cached(() => fetchProcurements({ state: "open" }));
  offs.push(
    registerCommandSource(
      "procurements",
      async (query): Promise<CommandItem[]> =>
        top(await procurements(), query, (p: ProcurementRow) => `${p.label} ${p.number} ${p.title} ${p.customer} ${p.contract_number}`).map((p) => ({
          id: `procurement:${p.id}`,
          title: `${p.label} · ${p.customer}`,
          subtitle: [p.title, p.law_display, p.deadline && `до ${ruDate(p.deadline)}`].filter(Boolean).join(" · "),
          icon: "contract" as const,
          run: () => go("procurements", { action: "open-procurement", payload: p.id }),
        })),
      { group: "Закупки", order: 30 },
    ),
  );

  const trips = cached(() => fetchTrips({ state: "open" }));
  offs.push(
    registerCommandSource(
      "trips",
      async (query): Promise<CommandItem[]> =>
        top(await trips(), query, (t: TripBrief) => `${t.title} ${t.destination} ${t.lead.full_name} ${t.procurement?.label ?? ""}`).map((t) => {
          const mode = t.kind === "city" ? "city" : "trips";
          return {
            id: `trip:${t.id}`,
            title: t.title,
            subtitle: [t.kind_display, t.status_display, ruDate(t.date_start), t.lead.full_name].filter(Boolean).join(" · "),
            icon: t.kind === "city" ? ("route" as const) : ("car" as const),
            run: () => go(mode, { action: "open-trip", payload: t.id }),
          };
        }),
      { group: "Выезды и командировки", order: 40 },
    ),
  );

  // «Приёмки»: по заказчику / номеру приёмки / заводскому номеру (modes/intake/commands.ts).
  offs.push(registerIntakeSources(go));

  return () => offs.forEach((off) => off());
}
