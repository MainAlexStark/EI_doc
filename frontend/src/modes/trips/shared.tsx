/** Общее для режимов «Город» и «Командировки»: выезд с офлайн-копией, отметки с «Отменить»,
 *  индикатор связи и очереди, моё место. Экранные куски (адрес, лист адреса, маршрут дня)
 *  живут в components/TripDetail.tsx и переиспользуются отсюда. */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTrip, updateTripStop, type StopStatus, type Trip, type TripStop } from "../../api";
import { flush, pendingCount, subscribe } from "../../offline/sync";
import Icon from "../../ui/Icon";
import { toast } from "../../ui/Toast";
import { errorText, type Point } from "../../components/tripUtil";

const CACHE = "ei_doc_trip_cache_";

function saveCopy(trip: Trip) {
  try {
    localStorage.setItem(CACHE + trip.id, JSON.stringify({ at: Date.now(), trip }));
  } catch {
    /* хранилище переполнено — без копии */
  }
}

export function cachedTrip(id: number): { at: number; trip: Trip } | null {
  try {
    return JSON.parse(localStorage.getItem(CACHE + id) ?? "null");
  } catch {
    return null;
  }
}

/** Выезд с копией на телефоне: на режимном объекте и в районе без связи список адресов
 *  остаётся виден (последний загруженный), а отметки и поверки уходят, когда связь вернётся. */
export function useTrip(id: number | null) {
  const [trip, setTripState] = useState<Trip | null>(() => (id != null ? cachedTrip(id)?.trip ?? null : null));
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [stale, setStale] = useState<number | null>(null); // показана копия от …
  const [loading, setLoading] = useState(false);
  const current = useRef(id);
  current.current = id;

  const setTrip = useCallback((next: Trip | ((prev: Trip | null) => Trip | null)) => {
    setTripState((prev) => {
      const value = typeof next === "function" ? next(prev) : next;
      if (value) saveCopy(value);
      return value;
    });
  }, []);

  const reload = useCallback(async () => {
    if (id == null) return null;
    setLoading(true);
    try {
      const fresh = await fetchTrip(id);
      if (current.current !== id) return null;
      setTrip(fresh);
      setError("");
      setStale(null);
      setMissing(false);
      return fresh;
    } catch (exc) {
      if (current.current !== id) return null;
      const status = (exc as { status?: number }).status;
      if (status === 404) setMissing(true);
      else {
        const copy = cachedTrip(id);
        if (copy && exc instanceof TypeError) {
          setTripState(copy.trip);
          setStale(copy.at);
        } else setError(errorText(exc, "Не удалось загрузить выезд"));
      }
      return null;
    } finally {
      if (current.current === id) setLoading(false);
    }
  }, [id, setTrip]);

  useEffect(() => {
    setMissing(false);
    setError("");
    setStale(null);
    setTripState(id != null ? cachedTrip(id)?.trip ?? null : null);
    void reload();
  }, [id, reload]);

  const patchStop = useCallback((fresh: TripStop) => {
    setTrip((prev) => (prev ? { ...prev, stops: prev.stops.map((stop) => (stop.id === fresh.id ? fresh : stop)) } : prev));
  }, [setTrip]);

  return { trip, setTrip, reload, error, setError, missing, stale, loading, patchStop };
}

const MARK_TEXT: Record<StopStatus, string> = {
  planned: "Адрес снова ждёт",
  done: "Выполнен",
  no_answer: "Не открыли",
  postponed: "Перенос",
  refused: "Отказ",
};

/** Отметка одним касанием, с «Отменить» в тосте. */
export async function markStop(
  stop: TripStop, status: StopStatus, apply: (fresh: TripStop) => void, after?: () => void,
): Promise<void> {
  const before = stop.status;
  try {
    apply(await updateTripStop(stop.id, { status }));
    after?.();
    toast[status === "done" ? "success" : "info"](`${MARK_TEXT[status]}: ${stop.address}`, {
      undo: before === status ? undefined : async () => {
        try {
          apply(await updateTripStop(stop.id, { status: before }));
          after?.();
        } catch (exc) {
          toast.error("Не удалось отменить", { description: errorText(exc, "") });
        }
      },
    });
  } catch (exc) {
    toast.error("Отметка не сохранилась", {
      description: exc instanceof TypeError ? "Нет связи — отметьте ещё раз, когда появится сеть" : errorText(exc, ""),
    });
  }
}

/** Моё место, пока экран открыт и нужно (карта, «следующий адрес»). */
export function useMyPoint(active: boolean): [Point | null, (p: Point) => void] {
  const [point, setPoint] = useState<Point | null>(null);
  useEffect(() => {
    if (!active || !("geolocation" in navigator)) return;
    const watch = navigator.geolocation.watchPosition(
      (position) => setPoint({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 30_000 },
    );
    return () => navigator.geolocation.clearWatch(watch);
  }, [active]);
  return [point, setPoint];
}

/** Есть ли связь и что ждёт отправки (поверки и измерения из офлайн-очереди). */
export function useOffline() {
  const [online, setOnline] = useState(typeof navigator === "undefined" || navigator.onLine);
  const [queued, setQueued] = useState(0);
  useEffect(() => {
    const refresh = () => void pendingCount().then(setQueued).catch(() => undefined);
    const on = () => { setOnline(true); refresh(); };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    refresh();
    const unsub = subscribe(refresh);
    const timer = window.setInterval(refresh, 30_000);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      unsub();
      window.clearInterval(timer);
    };
  }, []);
  return { online, queued };
}

export function OfflineBadge({ stale }: { stale?: number | null }) {
  const { online, queued } = useOffline();
  if (online && !queued && !stale) return null;
  const when = stale ? new Date(stale).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
  return (
    <div className={`offline-strip ${online ? "" : "is-off"}`} role="status">
      <Icon name={online ? "wifi" : "wifiOff"} size={15} />
      <span className="grow">
        {!online ? "Нет связи. " : ""}
        {stale ? `Показана копия от ${when}. ` : ""}
        {queued > 0 ? `Ждут отправки: ${queued} (поверки и измерения).` : !online ? "Поверки сохранятся на телефоне и уйдут сами." : ""}
      </span>
      {queued > 0 && online && (
        <button type="button" className="sm" onClick={() => void flush()}>Отправить</button>
      )}
    </div>
  );
}

/** Свайп влево/вправо по области — день вперёд/назад. */
export function useSwipe(onLeft: () => void, onRight: () => void) {
  const start = useRef<{ x: number; y: number } | null>(null);
  return {
    onTouchStart: (event: React.TouchEvent) => {
      const t = event.touches[0];
      start.current = { x: t.clientX, y: t.clientY };
    },
    onTouchEnd: (event: React.TouchEvent) => {
      const from = start.current;
      start.current = null;
      if (!from) return;
      const t = event.changedTouches[0];
      const dx = t.clientX - from.x;
      const dy = t.clientY - from.y;
      if (Math.abs(dx) < 70 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
      if (dx < 0) onLeft();
      else onRight();
    },
  };
}

/** Город адреса для фильтра «Киров / Яранск». */
export type Town = "all" | "kirov" | "yaransk";
export const townOf = (address: string): Exclude<Town, "all"> => (/яранск/i.test(address) ? "yaransk" : "kirov");

/** «8–10» из окна адреса. */
export const shortWindow = (stop: { window_start: string | null; window_end: string | null }) => {
  if (!stop.window_start) return "";
  const h = (t: string) => (t.endsWith(":00") ? String(Number(t.slice(0, 2))) : t.replace(/^0/, ""));
  return stop.window_end ? `${h(stop.window_start)}–${h(stop.window_end)}` : `с ${h(stop.window_start)}`;
};

/** Яндекс Навигатор (приложение) с запасным вариантом — Яндекс Карты. */
export const navigatorUrl = (stop: { latitude: number | null; longitude: number | null; address: string }) =>
  stop.latitude != null && stop.longitude != null
    ? `yandexnavi://build_route_on_map?lat_to=${stop.latitude}&lon_to=${stop.longitude}`
    : "";

export function openNavigator(stop: { latitude: number | null; longitude: number | null; address: string }) {
  const maps = stop.latitude != null && stop.longitude != null
    ? `https://yandex.ru/maps/?rtext=~${stop.latitude},${stop.longitude}&rtt=auto`
    : `https://yandex.ru/maps/?text=${encodeURIComponent(stop.address)}`;
  const touch = typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches;
  const navi = navigatorUrl(stop);
  if (touch && navi) {
    // Нет приложения — через секунду открываем Карты в браузере.
    const timer = window.setTimeout(() => window.open(maps, "_blank", "noopener"), 1200);
    const cancel = () => { if (document.hidden) window.clearTimeout(timer); };
    document.addEventListener("visibilitychange", cancel, { once: true });
    window.location.href = navi;
    return;
  }
  window.open(maps, "_blank", "noopener");
}
