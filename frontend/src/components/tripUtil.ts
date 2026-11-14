import type { StopStatus, TripBrief, TripStop } from "../api";

export type Point = { latitude: number; longitude: number };

const day = (value: string) => new Date(`${value}T00:00:00`);

export const isoDay = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

export const today = () => isoDay(new Date());

export const ruDate = (value: string | null | undefined, withYear = false) =>
  value
    ? day(value).toLocaleDateString("ru-RU", { day: "numeric", month: "long", ...(withYear ? { year: "numeric" } : {}) })
    : "";

export const ruShort = (value: string | null | undefined) =>
  value ? day(value).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }) : "";

/** «14–15 ноября», «30 ноября — 2 декабря», «с 14 ноября», «без дат». */
export function dateRange(start: string | null, end: string | null): string {
  if (!start && !end) return "без дат";
  if (start && !end) return `с ${ruDate(start)}`;
  if (!start && end) return `до ${ruDate(end)}`;
  if (start === end) return ruDate(start);
  const a = day(start!);
  const b = day(end!);
  if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
    return `${a.getDate()}–${ruDate(end)}`;
  }
  return `${ruDate(start)} — ${ruDate(end)}`;
}

export const daysUntil = (value: string | null | undefined): number | null => {
  if (!value) return null;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((day(value).getTime() - now.getTime()) / 86_400_000);
};

export const plural = (n: number, one: string, few: string, many: string) => {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};

export const instruments = (n: number) => `${n} ${plural(n, "прибор", "прибора", "приборов")}`;
export const addresses = (n: number) => `${n} ${plural(n, "адрес", "адреса", "адресов")}`;

export const STOP_TONE: Record<StopStatus, string> = {
  planned: "muted",
  done: "ok",
  no_answer: "warn",
  postponed: "purple",
  refused: "bad",
};

export const TRIP_TONE: Record<TripBrief["status"], string> = {
  planned: "info",
  active: "ok",
  done: "muted",
  cancelled: "bad",
};

/** Где я. Отказ или отсутствие GPS — не ошибка экрана: просто нет точки. */
export function locateMe(timeout = 12_000): Promise<Point> {
  return new Promise((resolve, reject) => {
    if (!("geolocation" in navigator)) {
      reject(new Error("Телефон не отдаёт местоположение"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
      (error) =>
        reject(
          new Error(
            error.code === error.PERMISSION_DENIED
              ? "Доступ к местоположению запрещён — разрешите его для сайта в настройках браузера"
              : "Не удалось определить местоположение",
          ),
        ),
      { enableHighAccuracy: true, timeout, maximumAge: 30_000 },
    );
  });
}

export const hasPoint = (stop: { latitude: number | null; longitude: number | null }): stop is Point =>
  stop.latitude != null && stop.longitude != null;

/** Расстояние по прямой, км. Для порядка объезда этого достаточно. */
export function distanceKm(a: Point, b: Point): number {
  const rad = Math.PI / 180;
  const dLat = (b.latitude - a.latitude) * rad;
  const dLon = (b.longitude - a.longitude) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

/** Порядок объезда «каждый раз к ближайшему» — от моего места или от первого адреса.
 * Отработанные адреса остаются впереди как были, адреса без точки уходят в конец. */
export function nearestOrder(stops: TripStop[], from: Point | null): number[] {
  const finished = stops.filter((stop) => stop.status !== "planned");
  const waiting = stops.filter((stop) => stop.status === "planned");
  const located = waiting.filter(hasPoint);
  const blind = waiting.filter((stop) => !hasPoint(stop));
  const route: TripStop[] = [];
  let current: Point | null = from;
  const pool = [...located];
  if (!current && pool.length) {
    const first = pool.shift()!;
    route.push(first);
    current = first as Point;
  }
  while (pool.length && current) {
    let best = 0;
    for (let index = 1; index < pool.length; index += 1) {
      if (distanceKm(current, pool[index] as Point) < distanceKm(current, pool[best] as Point)) best = index;
    }
    const [next] = pool.splice(best, 1);
    route.push(next);
    current = next as Point;
  }
  return [...finished, ...route, ...blind].map((stop) => stop.id);
}

/** Навигатор: Яндекс Карты открывают маршрут и в браузере, и в приложении. */
export const routeUrl = (points: Point[], from?: Point | null) => {
  const chain = [from ? `${from.latitude},${from.longitude}` : "", ...points.map((p) => `${p.latitude},${p.longitude}`)];
  return `https://yandex.ru/maps/?rtext=${chain.join("~")}&rtt=auto`;
};

export const searchUrl = (address: string) => `https://yandex.ru/maps/?text=${encodeURIComponent(address)}`;

export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

export const errorText = (exc: unknown, fallback: string) => (exc instanceof Error && exc.message ? exc.message : fallback);
