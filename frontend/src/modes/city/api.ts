/** API режима «Город»: дни поверителей, забор и возврат приборов (apps/logistics/api.py). */

import { json, type TripBrief, type TripStop, type WorkOrder } from "../../api";

/** Дни в городе, свежие сверху (сервер сортирует по дате убыванием). */
export const fetchCityDays = (around: string) => {
  // Окно ±14 дней вокруг выбранного дня: без него старые дни выпадали из лимита и показывались пустыми.
  const shift = (days: number) => {
    const d = new Date(`${around}T12:00:00`);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  };
  return json<TripBrief[]>(`/api/trips/?kind=city&limit=500&date_from=${shift(-14)}&date_to=${shift(14)}`);
};

export type PickupPayload = {
  client_id?: number | null;
  inn?: string;
  name?: string;
  address?: string;
  latitude?: number | null;
  longitude?: number | null;
  fias_id?: string;
  postal_code?: string;
  contact_name?: string;
  contact_phone?: string;
  planned_count?: number | null;
  date: string;
  window_start?: string | null;
  window_end?: string | null;
  employee_id?: number | null;
  note?: string;
};

export type StopWithTrip = TripStop & { trip_id: number; trip_title: string; employee: string };

export const createPickup = (payload: PickupPayload) =>
  json<StopWithTrip>("/api/pickups/", { method: "POST", body: JSON.stringify(payload) });

/** «Забрал»: адрес выполнен, заведена приёмка. Повтор отдаёт ту же. */
export const markPickedUp = (stopId: number, payload: { count?: number | null; note?: string }) =>
  json<{ order: WorkOrder; stop: TripStop }>(`/api/trip-stops/${stopId}/pickup/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const createDelivery = (orderId: number, payload: { date: string; employee_id?: number | null; window_start?: string | null; window_end?: string | null; note?: string }) =>
  json<StopWithTrip>(`/api/orders/${orderId}/delivery/`, { method: "POST", body: JSON.stringify(payload) });
