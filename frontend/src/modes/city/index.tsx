/** Режим 1 «Город» — день поверителя в Кирове и Яранске (docs/rebuild.md, «Город»).
 *
 *  Рассчитан на телефон в машине: переключатель дня (кнопки, свайп, клавиши [ ]),
 *  «следующий адрес» крупно с кнопками Позвонить / Навигатор / Поверять (Забрал, Вернул),
 *  отметки одним касанием с «Отменить», список адресов по маршруту с окнами, карта дня и
 *  «Маршрут дня» с пробками. Руководство видит дни всех поверителей и общую карту с назначением. */

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchTrip, type TripBrief, type TripStop } from "../../api";
import FieldWork from "../../components/FieldWork";
import {
  MARKS, PlanModal, RouteCard, StopRow, StopSheet, planDefaults, windowText,
} from "../../components/TripDetail";
import { addresses, errorText, hasPoint, instruments, isoDay, locateMe, routeUrl, telHref, type Point } from "../../components/tripUtil";
import VisitModal from "../../components/Visit";
import { planTripRoute } from "../../api";
import { registerCommandSource, scoreText } from "../../ui/CommandPalette";
import { EmptyState } from "../../ui/EmptyState";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import { Alert, Segmented } from "../../ui/controls";
import { usePersistent } from "../../ui/hooks";
import { useModeAction } from "../../ui/modeActions";
import { Sheet } from "../../ui/Sheet";
import { SkeletonList } from "../../ui/Skeleton";
import { toast } from "../../ui/Toast";
import { withViewTransition } from "../../ui/transition";
import type { ModeProps } from "../types";
import {
  OfflineBadge, markStop, openNavigator, shortWindow, townOf, useMyPoint, useSwipe, useTrip, type Town,
} from "../trips/shared";
import { fetchCityDays, markPickedUp } from "./api";
import PickupForm from "./PickupForm";
import "./city.css";

const TripMap = lazy(() => import("../../components/TripMap"));
const DispatchMap = lazy(() => import("../../components/control/DispatchMap"));

type View = "list" | "map" | "team";
const DATE_KEY = "ei_doc_city_date";

const shiftDay = (iso: string, delta: number) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + delta);
  return isoDay(d);
};

function dayTitle(iso: string): string {
  const today = isoDay(new Date());
  const label = new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" });
  if (iso === today) return `Сегодня, ${label}`;
  if (iso === shiftDay(today, 1)) return `Завтра, ${label}`;
  if (iso === shiftDay(today, -1)) return `Вчера, ${label}`;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

const loadDate = () => {
  try {
    const saved = sessionStorage.getItem(DATE_KEY);
    if (saved && /^\d{4}-\d{2}-\d{2}$/.test(saved)) return saved;
  } catch {
    /* приватный режим */
  }
  return isoDay(new Date());
};

export default function CityMode({ me, isManager, isControl, go }: ModeProps) {
  const [date, setDateState] = useState(loadDate);
  const [town, setTown] = usePersistent<Town>("ei_doc_city_town", "all", ["all", "kirov", "yaransk"]);
  const [savedView, setView] = usePersistent<View>("ei_doc_city_view", "list", ["list", "map", "team"]);
  const view: View = savedView === "team" && !isControl ? "list" : savedView;
  const [days, setDays] = useState<TripBrief[] | null>(null);
  const [daysError, setDaysError] = useState("");
  const [who, setWho] = useState<number | null>(null);
  const [creating, setCreating] = useState<null | "menu" | "visit" | "pickup">(null);
  const [openStop, setOpenStop] = useState<number | null>(null);
  const [working, setWorking] = useState<number | null>(null);
  const [pickupStop, setPickupStop] = useState<TripStop | null>(null);
  const [planning, setPlanning] = useState(false);
  const [busy, setBusy] = useState("");

  const setDate = useCallback((next: string) => {
    try {
      sessionStorage.setItem(DATE_KEY, next);
    } catch {
      /* не страшно */
    }
    withViewTransition(() => setDateState(next));
    setOpenStop(null);
  }, []);

  const reloadDays = useCallback(() => {
    setDaysError("");
    return fetchCityDays()
      .then((list) => { setDays(list); return list; })
      .catch((exc) => {
        setDaysError(errorText(exc, "Не удалось загрузить выезды"));
        return null;
      });
  }, []);
  useEffect(() => { void reloadDays(); }, [reloadDays]);

  // Дни этого числа: свой — первым, остальные (руководство) — по фамилии.
  const myId = me?.employee?.id ?? null;
  const dayTrips = useMemo(
    () =>
      (days ?? [])
        .filter((t) => t.date_start === date && t.status !== "cancelled")
        .sort((a, b) => Number(b.lead.id === myId) - Number(a.lead.id === myId) || a.lead.full_name.localeCompare(b.lead.full_name, "ru")),
    [days, date, myId],
  );
  const chosen = dayTrips.find((t) => t.lead.id === who) ?? dayTrips.find((t) => t.lead.id === myId) ?? (isManager ? dayTrips[0] : dayTrips.find((t) => t.members.some((m) => m.id === myId)) ?? dayTrips[0]);
  const tripId = chosen?.id ?? null;
  const { trip, setTrip, reload, error, setError, stale, patchStop } = useTrip(tripId);
  const current = trip && trip.id === tripId ? trip : null;
  const [myPoint, setMyPoint] = useMyPoint(view === "map" || !!current);

  /** Открыть конкретный выезд: перейти на его день и человека. */
  const focusTrip = useCallback(async (id: number) => {
    try {
      const t = await fetchTrip(id);
      if (t.kind !== "city") {
        go("trips", { action: "open-trip", payload: id });
        return;
      }
      await reloadDays();
      if (t.date_start) setDate(t.date_start);
      setWho(t.lead.id);
      if (view === "team") setView("list");
    } catch (exc) {
      toast.error("Не удалось открыть выезд", { description: errorText(exc, "") });
    }
  }, [go, reloadDays, setDate, view, setView]);

  useModeAction("city", {
    new: () => setCreating("menu"),
    "new-visit": () => setCreating("visit"),
    "new-pickup": () => setCreating("pickup"),
    "open-trip": (id) => typeof id === "number" && void focusTrip(id),
    "open-stop": (id) => typeof id === "number" && setOpenStop(id),
    today: () => setDate(isoDay(new Date())),
  });

  const back = () => setDate(shiftDay(date, -1));
  const forward = () => setDate(shiftDay(date, 1));
  useHotkey(["[", "х"], back, { description: "День назад", group: "Город" });
  useHotkey(["]", "ъ"], forward, { description: "День вперёд", group: "Город" });
  useHotkey("t", () => setDate(isoDay(new Date())), { description: "Сегодня", group: "Город" });
  useHotkey("m", () => setView(view === "map" ? "list" : "map"), { description: "Карта / список", group: "Город" });
  const swipe = useSwipe(forward, back);

  // Палитра команд: адреса открытого дня — по адресу, заказчику, телефону.
  const stopsRef = useRef<TripStop[]>([]);
  stopsRef.current = current?.stops ?? [];
  useEffect(
    () =>
      registerCommandSource(
        "city-stops",
        (query) =>
          stopsRef.current
            .map((s) => ({ s, score: scoreText(`${s.address} ${s.client} ${s.contact_name} ${s.contact_phone.replace(/\D/g, "")}`, query) }))
            .filter((row) => row.score > 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 6)
            .map(({ s }) => ({
              id: `city-stop:${s.id}`,
              title: s.address,
              subtitle: [s.status_display, s.window_start ? `ждут ${shortWindow(s)}` : "", s.client, s.contact_phone].filter(Boolean).join(" · "),
              icon: s.purpose === "pickup" ? ("box" as const) : ("mapPin" as const),
              run: () => go("city", { action: "open-stop", payload: s.id }),
            })),
        { group: "Город · адреса дня", order: 5 },
      ),
    [go],
  );

  if (!me) return <SkeletonList rows={5} />;

  // --- поверка по адресу: экран ввода приборов на наряде адреса ------------
  const workingStop = current?.stops.find((s) => s.id === working);
  if (current && workingStop?.work_order_id != null) {
    return (
      <FieldWork
        me={me}
        isManager={isManager}
        openOrderId={workingStop.work_order_id}
        backLabel={dayTitle(date)}
        fallbackTitle={workingStop.address}
        onClose={() => {
          setWorking(null);
          void reload();
          window.scrollTo({ top: 0 });
        }}
      />
    );
  }

  const stops = current?.stops ?? [];
  const towns = { kirov: stops.filter((s) => townOf(s.address) === "kirov").length, yaransk: stops.filter((s) => townOf(s.address) === "yaransk").length };
  const shown = town === "all" ? stops : stops.filter((s) => townOf(s.address) === town);
  const waiting = shown.filter((s) => s.status === "planned");
  const done = shown.filter((s) => s.status === "done").length;
  const missed = shown.filter((s) => s.status !== "planned" && s.status !== "done").length;
  const next = waiting[0] ?? null;
  const later = shown.filter((s) => s.id !== next?.id);
  const canEdit = !!current?.can_edit;
  const open = current ? current.status === "planned" || current.status === "active" : false;
  const waitingPoints = waiting.filter(hasPoint) as (TripStop & Point)[];
  const selected = stops.find((s) => s.id === openStop) ?? null;

  const mark = (stop: TripStop, status: TripStop["status"]) =>
    void markStop(stop, status, patchStop, () => void reload());

  const work = (stop: TripStop) => {
    if (stop.purpose === "pickup") {
      if (stop.lab_order_id) openIntake(stop.lab_order_id);
      else setPickupStop(stop);
      return;
    }
    if (stop.purpose === "delivery") {
      mark(stop, "done");
      return;
    }
    setOpenStop(null);
    setWorking(stop.id);
  };

  const openIntake = (id: number) => go("intake", { action: "open-order", payload: id, sub: String(id) });

  const primaryLabel = (stop: TripStop) =>
    stop.purpose === "pickup"
      ? stop.lab_order_id ? `Приёмка №${stop.lab_order_id}` : "Забрал"
      : stop.purpose === "delivery"
        ? "Вернул"
        : stop.verifications_total > 0 ? "Приборы" : "Поверять";
  const primaryIcon = (stop: TripStop) => (stop.purpose === "pickup" ? "box" : stop.purpose === "delivery" ? "truck" : "gauge") as "box" | "truck" | "gauge";

  const fromMe = () =>
    current &&
    (async () => {
      setBusy("plan");
      try {
        const point = myPoint ?? (await locateMe(8000));
        setMyPoint(point);
        const result = await planTripRoute(current.id, {
          ...planDefaults(current), start: "me", latitude: point.latitude, longitude: point.longitude,
          departure: new Date().toISOString(), apply: true,
        });
        if (result.trip) setTrip(result.trip);
        toast.success("Маршрут пересчитан от вашего места");
      } catch (exc) {
        toast.error("Не удалось пересчитать маршрут", { description: errorText(exc, "") });
      } finally {
        setBusy("");
      }
    })();

  // --- шапка: день, город, вид ------------------------------------------
  const head = (
    <div className="city-head">
      <div className="city-day-nav">
        <button type="button" className="icon-btn" aria-label="День назад" onClick={back}><Icon name="chevronLeft" size={20} /></button>
        <label className="city-day-title">
          <span>{dayTitle(date)}</span>
          <input type="date" value={date} onChange={(event) => event.target.value && setDate(event.target.value)} aria-label="Выбрать день" />
        </label>
        <button type="button" className="icon-btn" aria-label="День вперёд" onClick={forward}><Icon name="chevronRight" size={20} /></button>
        <span className="spacer" />
        {date !== isoDay(new Date()) && <button type="button" className="sm" onClick={() => setDate(isoDay(new Date()))}>Сегодня</button>}
        {date === isoDay(new Date()) && <button type="button" className="sm" onClick={forward}>Завтра</button>}
      </div>
      <div className="city-filters">
        <Segmented
          size="sm"
          value={town}
          onChange={setTown}
          options={[
            { value: "all", label: "Все", count: stops.length || undefined },
            { value: "kirov", label: "Киров", count: towns.kirov || undefined },
            { value: "yaransk", label: "Яранск", count: towns.yaransk || undefined },
          ]}
        />
        <span className="spacer" />
        <Segmented
          size="sm"
          value={view}
          onChange={setView}
          options={[
            { value: "list" as View, label: "Список", icon: "list" as const },
            { value: "map" as View, label: "Карта", icon: "map" as const },
            ...(isControl ? [{ value: "team" as View, label: "Все", icon: "users" as const }] : []),
          ]}
        />
      </div>
      {dayTrips.length > 1 && view !== "team" && (
        <div className="chip-row city-people" role="tablist" aria-label="Чей день">
          {dayTrips.map((t) => (
            <button
              type="button"
              key={t.id}
              role="tab"
              aria-selected={t.id === tripId}
              className={`chip ${t.id === tripId ? "active" : ""}`}
              onClick={() => setWho(t.lead.id)}
            >
              {t.lead.id === myId ? "Я" : t.lead.full_name.split(" ")[0]}
              <small className="data"> {t.stops_done}/{t.stops_total}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  const modals = (
    <>
      {creating === "menu" && (
        <Sheet open size="sm" onClose={() => setCreating(null)} title="Что записать?">
          <div className="city-create">
            <button type="button" className="city-create-btn" onClick={() => setCreating("visit")}>
              <Icon name="phone" size={22} />
              <span><b>Запись</b><small>Клиент позвонил — поверка у него дома, окно 2 часа</small></span>
            </button>
            <button type="button" className="city-create-btn" onClick={() => setCreating("pickup")}>
              <Icon name="box" size={22} />
              <span><b>Забрать коробку</b><small>Предприятие отдаёт приборы на поверку в лабораторию</small></span>
            </button>
          </div>
        </Sheet>
      )}
      {creating === "visit" && (
        <VisitModal
          me={me}
          onClose={() => setCreating(null)}
          onCreated={(result) => {
            setCreating(null);
            toast.success(`Записано: ${result.trip_title}`, { description: `Исполнитель — ${result.employee}` });
            void focusTrip(result.trip_id);
          }}
        />
      )}
      {creating === "pickup" && (
        <PickupForm
          me={me}
          date={date}
          onClose={() => setCreating(null)}
          onCreated={(result) => {
            setCreating(null);
            toast.success(`Забор записан: ${result.trip_title}`, { description: `${result.client} · ${result.employee}` });
            void focusTrip(result.trip_id);
          }}
        />
      )}
      {pickupStop && (
        <PickupDone
          stop={pickupStop}
          onClose={() => setPickupStop(null)}
          onDone={(order, fresh) => {
            setPickupStop(null);
            setOpenStop(null);
            patchStop(fresh);
            void reload();
            toast.success(`Приёмка №${order.id} создана`, {
              description: `${order.client} — приборы едут в лабораторию`,
              action: { label: "Открыть", onClick: () => openIntake(order.id) },
            });
          }}
        />
      )}
      {selected && current && (
        <StopSheet
          stop={selected}
          trip={current}
          canEdit={canEdit}
          busy={!!busy}
          index={stops.findIndex((s) => s.id === selected.id)}
          total={stops.length}
          onClose={() => setOpenStop(null)}
          onWork={() => work(selected)}
          onMark={(status) => mark(selected, status)}
          onMove={() => undefined}
          onRemove={() => undefined}
          onSaved={patchStop}
          onPlaceOnMap={() => { setOpenStop(null); setView("map"); }}
          onError={setError}
          primary={
            selected.purpose && selected.purpose !== "visit" ? (
              <button type="button" className="primary big" disabled={selected.purpose === "delivery" && selected.status === "done"} onClick={() => work(selected)}>
                <Icon name={primaryIcon(selected)} /> {primaryLabel(selected)}
              </button>
            ) : undefined
          }
        />
      )}
      {planning && current && (
        <PlanModal
          trip={current}
          me={myPoint}
          onClose={() => setPlanning(false)}
          onApplied={(fresh, text) => {
            setPlanning(false);
            setTrip(fresh);
            toast.success("Порядок объезда сохранён", { description: text });
          }}
        />
      )}
    </>
  );

  // --- руководство: карта всех поверителей ------------------------------
  if (view === "team") {
    return (
      <div className="city">
        {head}
        <Suspense fallback={<SkeletonList rows={3} label="Загружаю карту…" />}>
          <DispatchMap onOpenTrip={(id) => void focusTrip(id)} />
        </Suspense>
        {modals}
      </div>
    );
  }

  const nextCard = next && (
    <section className={`city-next ${next.purpose ?? "visit"}`} aria-label="Следующий адрес">
      <div className="city-next-top">
        <span className="city-next-label">
          {next.purpose === "pickup" ? <><Icon name="box" size={14} /> Забрать приборы</> : next.purpose === "delivery" ? <><Icon name="truck" size={14} /> Вернуть приборы</> : "Следующий адрес"}
        </span>
        {next.window_start && <span className="city-window">ждут {shortWindow(next)}</span>}
        {next.eta && <span className="city-eta">≈ {new Date(next.eta).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</span>}
      </div>
      <button type="button" className="city-next-address" onClick={() => setOpenStop(next.id)}>
        <b>{next.address}</b>
        <span>
          {[next.client !== "Физическое лицо" ? next.client : "", next.contact_name !== next.client ? next.contact_name : "",
            next.planned_count ? (next.purpose === "pickup" ? `≈ ${instruments(next.planned_count)}` : `ждут ${instruments(next.planned_count)}`) : "",
            next.note].filter(Boolean).join(" · ")}
        </span>
      </button>
      {next.is_restricted && <div className="city-restricted"><Icon name="lock" size={14} /> Режимный объект{next.access_note ? `: ${next.access_note}` : ""}</div>}
      <div className="city-big-actions">
        {next.contact_phone ? (
          <a className="btn big" href={telHref(next.contact_phone)}><Icon name="phone" /> Позвонить</a>
        ) : (
          <button type="button" className="big" disabled><Icon name="phone" /> Нет телефона</button>
        )}
        <button type="button" className="big" onClick={() => openNavigator(next)}><Icon name="route" /> Навигатор</button>
        <button type="button" className="primary big" disabled={!canEdit && next.purpose !== "visit"} onClick={() => work(next)}>
          <Icon name={primaryIcon(next)} /> {primaryLabel(next)}
        </button>
      </div>
      {canEdit && open && (
        <div className="city-marks">
          {MARKS.filter((m) => m.status !== "done" || next.purpose !== "pickup").map((m) => (
            <button type="button" key={m.status} className={`trip-mark ${m.status}`} onClick={() => mark(next, m.status)}>
              <Icon name={m.icon} size={16} /> {m.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );

  const progress = current && shown.length > 0 && (
    <div className="city-progress" aria-label="Прогресс дня">
      <div className="city-progress-text">
        <b className="data">{done}</b><span> из {shown.length} сделано</span>
        {missed > 0 && <span className="sub"> · не состоялось {missed}</span>}
        {current.summary.instruments_entered > 0 && <span className="sub"> · {instruments(current.summary.instruments_entered)}</span>}
      </div>
      <div className="city-bar">
        <i className="ok" style={{ width: `${(done / shown.length) * 100}%` }} />
        <i className="miss" style={{ width: `${(missed / shown.length) * 100}%` }} />
      </div>
    </div>
  );

  const routeBar = current && waiting.length > 0 && (
    <div className="city-route-bar">
      {canEdit && waitingPoints.length > 1 && (
        <button type="button" className="sm tinted" disabled={!!busy} onClick={() => setPlanning(true)} title="Порядок объезда с пробками 2ГИС и окнами клиентов">
          <Icon name="bolt" size={14} /> Маршрут дня
        </button>
      )}
      {waitingPoints.length > 0 && (
        <a className="btn sm" href={routeUrl(waitingPoints.slice(0, 14), myPoint)} target="_blank" rel="noreferrer">
          <Icon name="external" size={14} /> Весь день в Яндекс Картах
        </a>
      )}
    </div>
  );

  const list = (
    <div className="city-list">
      {later.length > 0 && <div className="list-section">{next ? "Дальше по маршруту" : "Адреса дня"} <span className="count">{later.length}</span></div>}
      <div className="card-list">
        {later.map((stop) => (
          <div key={stop.id} className={`city-row ${stop.status !== "planned" ? "is-closed" : ""}`}>
            <StopRow stop={stop} me={myPoint} active={stop.id === openStop} onOpen={() => setOpenStop(stop.id)} />
            {stop.status === "planned" && (
              <div className="city-row-actions">
                {stop.contact_phone && <a className="icon-btn" href={telHref(stop.contact_phone)} aria-label="Позвонить"><Icon name="phone" size={17} /></a>}
                <button type="button" className="icon-btn" aria-label="Навигатор" onClick={() => openNavigator(stop)}><Icon name="route" size={17} /></button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  let body;
  if (days == null && !daysError) body = <SkeletonList rows={4} label="Собираю адреса дня…" />;
  else if (daysError && !current) body = <EmptyState tone="error" icon="wifiOff" title="Адреса не загрузились" action={{ label: "Повторить", icon: "refresh", onClick: () => void reloadDays() }}>{daysError}</EmptyState>;
  else if (!chosen) {
    body = (
      <EmptyState
        icon="route"
        title={`${dayTitle(date)} — адресов нет`}
        action={{ label: "Запись", icon: "phone", onClick: () => setCreating("visit"), hotkey: "N" }}
        secondary={{ label: "Забрать коробку", icon: "box", onClick: () => setCreating("pickup") }}
      >
        Подтверждённые заявки с сайта встают в день сами. Клиент позвонил — «Запись»; предприятие отдаёт приборы — «Забрать коробку».
      </EmptyState>
    );
  } else if (!current) body = error ? <Alert>{error}</Alert> : <SkeletonList rows={4} />;
  else if (shown.length === 0) {
    body = <EmptyState compact icon="filter" title={`В ${town === "yaransk" ? "Яранске" : "Кирове"} на этот день адресов нет`} action={{ label: "Показать все", onClick: () => setTown("all") }} />;
  } else {
    body = (
      <>
        {progress}
        {waiting.length === 0 && (
          <div className="city-all-done"><Icon name="checkCircle" size={20} /> Все адреса отмечены{missed ? ` · не состоялось ${addresses(missed)}` : ""}</div>
        )}
        {nextCard}
        {current.route && waiting.length > 0 && (
          <RouteCard route={current.route} canEdit={canEdit} busy={!!busy} onReplan={() => setPlanning(true)} onFromMe={() => void fromMe()} />
        )}
        {routeBar}
        {view === "map" ? (
          <div className="trip-split city-split">
            <div className="trip-map-wrap">
              <Suspense fallback={<SkeletonList rows={2} label="Загружаю карту…" />}>
                <TripMap stops={shown} selectedId={openStop} me={myPoint} onSelect={(s) => setOpenStop(s.id)} />
              </Suspense>
            </div>
            {list}
          </div>
        ) : list}
        {next?.window_start && next.window_end && <p className="hint city-foot">Окно ближайшего: {windowText(next)}. Опаздываете — позвоните заранее.</p>}
      </>
    );
  }

  return (
    <div className={`city ${view === "map" ? "is-wide" : ""}`} {...swipe}>
      {head}
      <OfflineBadge stale={stale} />
      {error && current && <Alert onClose={() => setError("")}>{error}</Alert>}
      {body}
      {modals}
    </div>
  );
}

/** «Забрал»: сколько приборов на самом деле — и сразу приёмка в лаборатории. */
function PickupDone({
  stop, onClose, onDone,
}: {
  stop: TripStop;
  onClose: () => void;
  onDone: (order: { id: number; client: string }, stop: TripStop) => void;
}) {
  const [count, setCount] = useState(stop.planned_count ? String(stop.planned_count) : "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await markPickedUp(stop.id, { count: count ? Number(count) : null, note: note.trim() });
      onDone(result.order, result.stop);
    } catch (exc) {
      setError(exc instanceof TypeError ? "Нет связи — приёмку заведём, когда появится сеть: нажмите ещё раз" : errorText(exc, "Не удалось отметить"));
      setBusy(false);
    }
  };
  return (
    <Sheet
      open
      size="sm"
      onClose={onClose}
      title={<><Icon name="box" size={18} /> Забрал приборы</>}
      subtitle={`${stop.client} · ${stop.address}`}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Завожу приёмку…" : "Забрал"}</button>
        </>
      }
    >
      <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <div className="field">
          <label htmlFor="picked-count">Сколько приборов забрали?</label>
          <input id="picked-count" className="big-input data" autoFocus inputMode="numeric" value={count}
                 onChange={(event) => setCount(event.target.value.replace(/\D/g, ""))} placeholder={stop.planned_count ? String(stop.planned_count) : "например, 12"} />
          <div className="chip-row">
            {[5, 10, 20, 50].map((n) => (
              <button type="button" key={n} className={`chip ${count === String(n) ? "active" : ""}`} onClick={() => setCount(String(n))}>{n}</button>
            ))}
          </div>
        </div>
        <div className="field">
          <label htmlFor="picked-note">Заметка <span className="opt">— необязательно</span></label>
          <input id="picked-note" value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} placeholder="Две коробки, один без паспорта…" />
        </div>
        <p className="hint">Адрес отметится выполненным, в «Приёмках» появится заказ с этим заказчиком — приборы впишете в лаборатории.</p>
        {error && <Alert>{error}</Alert>}
      </form>
    </Sheet>
  );
}
