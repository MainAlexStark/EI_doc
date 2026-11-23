/** Палитра команд (⌘K): типы СИ (название, номер в Госреестре, изготовитель), шаблоны поверки и шаблоны
 *  аттестации — открыть в своих режимах. Регистрирует оболочка (App.tsx) рядом с источниками по умолчанию. */

import { fetchSiTypes, type VerificationTemplate } from "../../api";
import { registerCommandSource, scoreText, type CommandItem } from "../../ui/CommandPalette";
import { fetchPrograms, type Program } from "../att-templates/api";
import type { Access, Go } from "../types";
import { STATUS_LABEL, allTemplates, templateStatus } from "./api";

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

function top<T>(list: T[], query: string, text: (item: T) => string, limit = 6): T[] {
  return list
    .map((item) => ({ item, score: scoreText(text(item), query) }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((row) => row.item);
}

export function registerTemplateSources(go: Go, access: Access): () => void {
  const offs: (() => void)[] = [];

  offs.push(
    registerCommandSource(
      "si-types",
      async (query, signal): Promise<CommandItem[]> => {
        if (signal.aborted) return [];
        const list = await fetchSiTypes({ q: query.trim() });
        return list.slice(0, 6).map((t) => ({
          id: `si-type:${t.id}`,
          title: `${t.name} — шаблон поверки`,
          subtitle: [`№ ${t.registry_number}`, t.manufacturer, t.family_name, t.needs_review ? "тип ждёт проверки" : ""].filter(Boolean).join(" · "),
          mono: true,
          icon: "gauge" as const,
          keywords: `${t.registry_number} ${t.manufacturer} ${t.type_title}`,
          run: () => go("si-templates", { action: "open-type", payload: t.id }),
        }));
      },
      { group: "Типы СИ · шаблоны поверки", minQuery: 2, order: 25 },
    ),
  );

  offs.push(
    registerCommandSource(
      "verification-templates",
      async (query): Promise<CommandItem[]> =>
        top(await allTemplates(), query, (t: VerificationTemplate) => `${t.title} ${t.si_type_name} ${t.family_name} ${t.plan?.method ?? ""}`)
          .filter((t) => t.is_active)
          .map((t) => ({
            id: `vtemplate:${t.id}`,
            title: t.title,
            subtitle: [t.si_type_name || `все типы: ${t.family_name}`, STATUS_LABEL[templateStatus(t)], t.plan?.method].filter(Boolean).join(" · "),
            icon: "layers" as const,
            run: () => go("si-templates", { action: "open-template", payload: t.id }),
          })),
      { group: "Шаблоны поверки", minQuery: 2, order: 26 },
    ),
  );

  if (access.isManager) {
    const programs = cached(() => fetchPrograms({ plan: false }));
    offs.push(
      registerCommandSource(
        "att-programs",
        async (query): Promise<CommandItem[]> =>
          top(await programs(), query, (p: Program) => `${p.title} ${p.equipment_kind} аттестация`).map((p) => ({
            id: `att-program:${p.id}`,
            title: p.title,
            subtitle: [p.equipment_kind, p.needs_review ? "на проверке" : "проверен", p.used ? `применён ${p.used} раз` : ""].filter(Boolean).join(" · "),
            icon: "shield" as const,
            run: () => go("att-templates", { action: "open-program", payload: p.id }),
          })),
        { group: "Шаблоны аттестации", minQuery: 2, order: 27 },
      ),
    );
  }

  return () => offs.forEach((off) => off());
}
