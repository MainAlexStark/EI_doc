/** API режима «Заявки»: разбор пачкой (POST /api/hub/requests/bulk/) и загрузка людей для подсказки. */

import { json, type HubRequest } from "../../api";
import { fetchOverview, type TeamCard } from "../../dispatchApi";

/** reopen — вернуть во «Новые» («Отменить» после отправки); нельзя, если по заявке есть поверки. */
export type BulkAction = "confirm" | "assign" | "reject" | "spam" | "reopen";
export type BulkResult = {
  done: number[];
  skipped: { id: number; reason: string }[];
  errors: { id: number; error: string }[];
  items: HubRequest[];
};

export function bulkRequests(
  action: BulkAction,
  ids: number[],
  extra: { employee_id?: number | null; scheduled_date?: string | null; scheduled_time?: string | null; reason?: string } = {},
  init: RequestInit = {},
): Promise<BulkResult> {
  return json<BulkResult>("/api/hub/requests/bulk/", {
    method: "POST",
    body: JSON.stringify({ action, ids, ...extra }),
    ...init,
  });
}

/** Кто сколько занят в день — для подсказки в пикере исполнителя. Нет прав на сводку — пусто. */
export async function fetchLoad(date?: string): Promise<Map<number, TeamCard>> {
  try {
    const overview = await fetchOverview(date);
    return new Map(overview.employees.map((e) => [e.id, e]));
  } catch {
    return new Map();
  }
}
