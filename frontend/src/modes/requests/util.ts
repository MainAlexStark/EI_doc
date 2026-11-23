/** Подписи и форматирование для режима «Заявки». */

import type { HubRequest } from "../../api";

export type Box = "new" | "work" | "archive";

export const BOX_STATUSES: Record<Box, string[]> = {
  new: ["new", "routed"],
  work: ["confirmed"],
  archive: ["done", "cancelled", "rejected", "spam"],
};
export const boxOf = (status: string): Box =>
  (Object.keys(BOX_STATUSES) as Box[]).find((box) => BOX_STATUSES[box].includes(status)) ?? "archive";

export const STATUS: Record<string, { label: string; tone: string }> = {
  new: { label: "Новая", tone: "info" },
  routed: { label: "Исполнитель подобран", tone: "info" },
  confirmed: { label: "Подтверждена", tone: "ok" },
  done: { label: "Выполнена", tone: "ok" },
  cancelled: { label: "Отменена", tone: "muted" },
  rejected: { label: "Отклонена", tone: "bad" },
  spam: { label: "Спам / дубль", tone: "muted" },
};

export const SOURCE: Record<string, string> = { site: "сайт", phone: "телефон", email: "почта", manual: "вручную" };

export const money = (value: string | number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(Number(value));

export const formatDay = (value: string | null) =>
  value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" }) : "";

/** Время визита — окном 2 часа: «10:00–12:00». */
export function timeWindow(time: string | null): string {
  if (!time) return "";
  const [h, m] = time.split(":").map(Number);
  const end = `${String(Math.min(23, h + 2)).padStart(2, "0")}:${String(m || 0).padStart(2, "0")}`;
  return `${time.slice(0, 5)}–${end}`;
}

export const whenLine = (r: HubRequest) =>
  r.desired_date ? [formatDay(r.desired_date), timeWindow(r.desired_time)].filter(Boolean).join(", ") : "";

export function receivedAt(value: string): string {
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "вчера";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
}

export const itemsLine = (r: HubRequest) =>
  r.items.length
    ? r.items.map((item) => `${item.family}${item.quantity > 1 ? ` × ${item.quantity}` : ""}`).join(", ")
    : r.si_description || "Приборы не указаны";

export const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};
export const nRequests = (n: number) => `${n} ${plural(n, "заявка", "заявки", "заявок")}`;

export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;
export const mapHref = (address: string) => `https://yandex.ru/maps/?text=${encodeURIComponent(address)}`;
