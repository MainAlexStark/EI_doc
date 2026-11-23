/** Режим 2 «Командировки» — поездка на несколько дней по району и соседним сёлам.
 *
 *  Список: идут сейчас → предстоящие → завершённые, у каждой прогресс. Внутри: шапка
 *  (район, даты, состав, закупка), вкладки по дням с адресами дня, карта всей поездки
 *  с цветом по дням, раскладка по дням (решатель planner), адрес «Я здесь» одним экраном,
 *  импорт списком, сводка план/факт, следующий заход, индикатор офлайна. */

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { deleteTrip, deleteTripStop, fetchTrips, locateTripStops, planTripRoute, reorderTripStops, updateTrip, updateTripStop, type Me, type TripBrief, type TripStop } from "../../api";
import FieldWork from "../../components/FieldWork";
import { DeadlinePill } from "../../components/TripBits";
import {
  AddStopModal, BulkModal, EditTripModal, NextVisitModal, PlanModal, RouteCard, StopRow, StopSheet, planDefaults,
} from "../../components/TripDetail";
import { NewTripModal } from "../../components/Trips";
import {
  TRIP_TONE, addresses, dateRange, errorText, hasPoint, instruments, isoDay, locateMe, routeUrl, today, type Point,
} from "../../components/tripUtil";
import { registerCommandSource, scoreText } from "../../ui/CommandPalette";
import { EmptyState } from "../../ui/EmptyState";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import { Alert, Segmented } from "../../ui/controls";
import { confirmDialog } from "../../ui/Modal";
import { usePersistent } from "../../ui/hooks";
import { useModeAction } from "../../ui/modeActions";
import { SkeletonList } from "../../ui/Skeleton";
import { toast } from "../../ui/Toast";
import { withViewTransition } from "../../ui/transition";
import type { Go, ModeProps } from "../types";
import { OfflineBadge, markStop, openNavigator, useMyPoint, useTrip } from "./shared";
import "./trips.css";

const TripMap = lazy(() => import("../../components/TripMap"));

type View = { kind: "list" } | { kind: "trip"; id: number };
const VIEW_KEY = "ei_doc_trips_view"; // сюда же пишут Панель и оболочка (go("trips", { trip }))

function loadView(): View {
  try {
    const saved = JSON.parse(sessionStorage.getItem(VIEW_KEY) ?? "null") as { kind?: string; id?: number } | null;
    if (saved?.kind === "trip" && Number.isInteger(saved.id)) return { kind: "trip", id: saved.id! };
  } catch {
    /* хранилище недоступно */
  }
  return { kind: "list" };
}

/** Цвета дней поездки — различимые на карте, из токенов там, где они есть. */
export const DAY_COLORS = ["#2b45e0", "#12925a", "#e89b00", "#9b5ed6", "#d3311f", "#0f8fa8", "#b35c1e", "#5c6272"];

export default function TripsMode({ me, isManager, go }: ModeProps) {
  const [view, setViewState] = useState<View>(loadView);
  const [creating, setCreating] = useState(false);

  const setView = useCallback((next: View) => {
    try {
      sessionStorage.setItem(VIEW_KEY, JSON.stringify(next));
    } catch {
      /* не страшно */
    }
    withViewTransition(() => setViewState(next));
    window.scrollTo({ top: 0 });
  }, []);

  useModeAction("trips", {
    new: () => setCreating(true),
    "new-trip": () => setCreating(true),
    "open-trip": (id) => typeof id === "number" && setView({ kind: "trip", id }),
    list: () => setView({ kind: "list" }),
  });

  if (!me) return <SkeletonList rows={4} />;

  return (
    <>
      {view.kind === "trip" ? (
        <TripView key={view.id} id={view.id} me={me} isManager={isManager} go={go} onBack={() => setView({ kind: "list" })} onOpen={(id) => setView({ kind: "trip", id })} />
      ) : (
        <TripList isManager={isManager} onOpen={(id) => setView({ kind: "trip", id })} onCreate={() => setCreating(true)} />
      )}
      {creating && (
        <NewTripModal
          me={me}
          isManager={isManager}
          procurementId={null}
          initialKind="classic"
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            toast.success("Командировка заведена", { description: "Добавьте адреса — по одному или списком" });
            setView({ kind: "trip", id });
          }}
          onNewProcurement={() => {
            setCreating(false);
            if (isManager) go("procurements", { action: "new" });
          }}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Список
// ---------------------------------------------------------------------------
function TripList({ isManager, onOpen, onCreate }: { isManager: boolean; onOpen: (id: number) => void; onCreate: () => void }) {
  const [open, setOpen] = useState<TripBrief[] | null>(null);
  const [closed, setClosed] = useState<TripBrief[] | null>(null);
  const [showClosed, setShowClosed] = usePersistent<"yes" | "no">("ei_doc_trips_closed", "no", ["yes", "no"]);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    setError("");
    fetchTrips({ state: "open" })
      .then((rows) => setOpen(rows.filter((t) => t.kind !== "city")))
      .catch((exc) => setError(errorText(exc, "Не удалось загрузить командировки")));
  }, []);
  useEffect(reload, [reload]);
  useEffect(() => {
    if (showClosed !== "yes" || closed) return;
    fetchTrips({ state: "closed" })
      .then((rows) => setClosed(rows.filter((t) => t.kind !== "city").slice(0, 40)))
      .catch(() => setClosed([]));
  }, [showClosed, closed]);

  const now = today();
  const groups = useMemo(() => {
    const list = open ?? [];
    const running = list.filter((t) => t.status === "active" || (t.date_start != null && t.date_start <= now && (t.date_end ?? t.date_start) >= now));
    const upcoming = list
      .filter((t) => !running.includes(t))
      .sort((a, b) => (a.date_start ?? "9999").localeCompare(b.date_start ?? "9999") || a.id - b.id);
    return { running, upcoming };
  }, [open, now]);

  if (error) return <EmptyState tone="error" icon="wifiOff" title="Командировки не загрузились" action={{ label: "Повторить", icon: "refresh", onClick: reload }}>{error}</EmptyState>;
  if (!open) return <div className="narrow"><SkeletonList rows={4} label="Загружаю командировки…" /></div>;

  return (
    <div className="narrow trips-list">
      <OfflineBadge />
      {open.length === 0 && (
        <EmptyState icon="car" title="Командировок в работе нет" action={{ label: "Новая командировка", icon: "plus", onClick: onCreate, hotkey: "N" }}>
          Район, даты, кто едет — адреса добавите списком или уже на месте, кнопкой «Я здесь».
        </EmptyState>
      )}
      {groups.running.length > 0 && (
        <>
          <div className="list-section">Идут сейчас <span className="count">{groups.running.length}</span></div>
          <div className="card-list">{groups.running.map((t) => <TripCard key={t.id} trip={t} showLead={isManager} onOpen={() => onOpen(t.id)} live />)}</div>
        </>
      )}
      {groups.upcoming.length > 0 && (
        <>
          <div className="list-section">Предстоящие <span className="count">{groups.upcoming.length}</span></div>
          <div className="card-list">{groups.upcoming.map((t) => <TripCard key={t.id} trip={t} showLead={isManager} onOpen={() => onOpen(t.id)} />)}</div>
        </>
      )}
      <button type="button" className="link trips-closed-toggle" onClick={() => setShowClosed(showClosed === "yes" ? "no" : "yes")}>
        <Icon name="history" size={14} /> {showClosed === "yes" ? "Скрыть завершённые" : "Показать завершённые"}
      </button>
      {showClosed === "yes" && (
        closed == null ? <SkeletonList rows={2} /> : closed.length === 0 ? <p className="hint">Завершённых пока нет.</p> : (
          <>
            <div className="list-section">Завершённые <span className="count">{closed.length}</span></div>
            <div className="card-list">{closed.map((t) => <TripCard key={t.id} trip={t} showLead={isManager} onOpen={() => onOpen(t.id)} />)}</div>
          </>
        )
      )}
    </div>
  );
}

function TripCard({ trip, showLead, onOpen, live }: { trip: TripBrief; showLead: boolean; onOpen: () => void; live?: boolean }) {
  const people = [trip.lead.full_name, ...trip.members.map((m) => m.full_name)];
  const pct = trip.stops_total ? Math.round((trip.stops_done / trip.stops_total) * 100) : 0;
  return (
    <button type="button" className={`card tap trips-card ${live ? "is-live" : ""}`} onClick={onOpen}>
      <div className="order-row">
        <div className="grow">
          <b>{trip.title}</b>
          <div className="sub">
            {[dateRange(trip.date_start, trip.date_end), trip.destination && trip.destination !== trip.title ? trip.destination : "",
              showLead ? (people.length > 1 ? `${people[0]} +${people.length - 1}` : people[0]) : ""].filter(Boolean).join(" · ")}
          </div>
          <div className="trip-tags">
            <span className={`pill ${TRIP_TONE[trip.status]}`}>{trip.status_display}</span>
            {trip.procurement && <span className="pill purple plain">Закупка {trip.procurement.label}</span>}
            {trip.procurement && <DeadlinePill deadline={trip.procurement.deadline} closed={trip.status === "done"} />}
          </div>
        </div>
        <div className="order-progress">
          <span className="data">{trip.stops_total > 0 ? `${trip.stops_done}/${trip.stops_total}` : "—"}</span>
          {trip.stops_total > 0 && <div className="mini-bar"><i style={{ width: `${pct}%` }} /></div>}
          {trip.instruments_entered > 0 && <small>{instruments(trip.instruments_entered)}</small>}
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Одна командировка
// ---------------------------------------------------------------------------
const NO_DAY = "none";
const dayTab = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" });

function tripDays(start: string | null, end: string | null): string[] {
  if (!start) return [];
  const out: string[] = [];
  const d = new Date(`${start}T12:00:00`);
  const last = end && end >= start ? end : start;
  while (isoDay(d) <= last && out.length < 31) {
    out.push(isoDay(d));
    d.setDate(d.getDate() + 1);
  }
  return out;
}

function TripView({
  id, me, isManager, go, onBack, onOpen,
}: {
  id: number;
  me: Me;
  isManager: boolean;
  go: Go;
  onBack: () => void;
  onOpen: (id: number) => void;
}) {
  const { trip, setTrip, reload, error, setError, missing, stale, patchStop } = useTrip(id);
  const [layout, setLayout] = usePersistent<"list" | "map">("ei_doc_trip_mode", "list", ["list", "map"]);
  const [day, setDay] = useState<string>("all");
  const [openStop, setOpenStop] = useState<number | null>(null);
  const [working, setWorking] = useState<number | null>(null);
  const [adding, setAdding] = useState<null | "one" | "bulk">(null);
  const [editing, setEditing] = useState(false);
  const [nextVisit, setNextVisit] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [busy, setBusy] = useState("");
  const [placing, setPlacing] = useState<number | null>(null);
  const [myPoint, setMyPoint] = useMyPoint(layout === "map");

  const days = useMemo(() => tripDays(trip?.date_start ?? null, trip?.date_end ?? null), [trip?.date_start, trip?.date_end]);
  const stops = useMemo(() => trip?.stops ?? [], [trip]);
  const dayOf = (s: TripStop) => (s.planned_date && days.includes(s.planned_date) ? s.planned_date : NO_DAY);
  const colorOf = useCallback(
    (s: TripStop) => {
      const index = s.planned_date ? days.indexOf(s.planned_date) : -1;
      return index >= 0 ? DAY_COLORS[index % DAY_COLORS.length] : days.length > 1 ? "#9aa0ad" : undefined;
    },
    [days],
  );
  const tabs = [
    { value: "all", label: "Все", count: stops.length },
    ...days.map((d) => ({ value: d, label: dayTab(d), count: stops.filter((s) => s.planned_date === d).length })),
    ...(days.length > 1 && stops.some((s) => dayOf(s) === NO_DAY) ? [{ value: NO_DAY, label: "Без дня", count: stops.filter((s) => dayOf(s) === NO_DAY).length }] : []),
  ];
  const shown = day === "all" ? stops : stops.filter((s) => dayOf(s) === day);
  const tabValues = tabs.map((t) => t.value);
  const step = (delta: number) => {
    const i = tabValues.indexOf(day);
    setDay(tabValues[Math.min(tabValues.length - 1, Math.max(0, i + delta))]);
  };
  useHotkey(["[", "х"], () => step(-1), { description: "День поездки назад", group: "Командировки" });
  useHotkey(["]", "ъ"], () => step(1), { description: "День поездки вперёд", group: "Командировки" });
  useHotkey("m", () => setLayout(layout === "map" ? "list" : "map"), { description: "Карта / список", group: "Командировки" });
  useHotkey("a", () => trip?.can_edit && setAdding("one"), { description: "Добавить адрес", group: "Командировки" });

  // Сегодняшний день поездки — сразу открытой вкладкой.
  const firstLoad = useRef(true);
  useEffect(() => {
    if (!trip || !firstLoad.current) return;
    firstLoad.current = false;
    if (days.length > 1 && days.includes(today())) setDay(today());
  }, [trip, days]);

  const stopsRef = useRef<TripStop[]>([]);
  stopsRef.current = stops;
  useEffect(
    () =>
      registerCommandSource(
        "trip-stops",
        (query) =>
          stopsRef.current
            .map((s) => ({ s, score: scoreText(`${s.address} ${s.client} ${s.contact_name} ${s.contact_phone.replace(/\D/g, "")}`, query) }))
            .filter((r) => r.score > 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 6)
            .map(({ s }) => ({
              id: `trip-stop:${s.id}`,
              title: s.address,
              subtitle: [s.status_display, s.planned_date ? dayTab(s.planned_date) : "", s.client].filter(Boolean).join(" · "),
              icon: "mapPin" as const,
              run: () => setOpenStop(s.id),
            })),
        { group: "Командировка · адреса", order: 5 },
      ),
    [],
  );

  if (missing) {
    return (
      <div className="narrow">
        <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Командировки</button>
        <EmptyState icon="car" title="Командировка не найдена" action={{ label: "К списку", onClick: onBack }}>Возможно, её удалили или вы в ней не участвуете.</EmptyState>
      </div>
    );
  }
  if (!trip) {
    return (
      <div className="narrow">
        <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Командировки</button>
        {error ? <EmptyState tone="error" icon="wifiOff" title="Не загрузилась" action={{ label: "Повторить", icon: "refresh", onClick: () => void reload() }}>{error}</EmptyState> : <SkeletonList rows={5} />}
      </div>
    );
  }

  const workingStop = stops.find((s) => s.id === working);
  if (workingStop?.work_order_id != null) {
    return (
      <FieldWork
        me={me}
        isManager={isManager}
        openOrderId={workingStop.work_order_id}
        backLabel={trip.title}
        fallbackTitle={workingStop.address}
        onClose={() => { setWorking(null); void reload(); window.scrollTo({ top: 0 }); }}
      />
    );
  }

  const canEdit = trip.can_edit;
  const open = trip.status === "planned" || trip.status === "active";
  const summary = trip.summary;
  const missed = summary.stops_no_answer + summary.stops_postponed + summary.stops_refused;
  const left = summary.stops_waiting + summary.stops_no_answer + summary.stops_postponed;
  const people = [trip.lead, ...trip.members];
  const waitingShown = shown.filter((s) => s.status === "planned");
  const waitingPoints = waitingShown.filter(hasPoint) as (TripStop & Point)[];
  const nextStop = (day === "all" ? stops : shown).find((s) => s.status === "planned") ?? null;
  const selected = stops.find((s) => s.id === openStop) ?? null;
  const unlocated = stops.filter((s) => !hasPoint(s)).length;

  const run = async (label: string, action: () => Promise<unknown>, failure: string) => {
    setBusy(label);
    try {
      await action();
    } catch (exc) {
      toast.error(failure, { description: errorText(exc, "") });
    } finally {
      setBusy("");
    }
  };
  const setStatus = (status: TripBrief["status"]) =>
    run("status", async () => setTrip(await updateTrip(trip.id, { status })), "Не удалось сменить состояние");
  const finish = async () => {
    const ok = await confirmDialog({
      title: "Завершить командировку?",
      text: summary.stops_waiting
        ? `${addresses(summary.stops_waiting)} ещё ждут.${trip.procurement ? " В следующий заход их можно перенести одной кнопкой." : ""}`
        : "Все адреса отмечены.",
      ok: "Завершить",
    });
    if (ok) void setStatus("done");
  };
  const move = (stop: TripStop, delta: number) => {
    const ids = stops.map((s) => s.id);
    const i = ids.indexOf(stop.id);
    const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    void run("order", async () => setTrip(await reorderTripStops(trip.id, ids)), "Не удалось поменять порядок");
  };
  const removeStop = async (stop: TripStop) => {
    if (!(await confirmDialog({ title: "Убрать адрес?", text: stop.address, ok: "Убрать", danger: true }))) return;
    void run("remove", async () => { await deleteTripStop(stop.id); setOpenStop(null); await reload(); }, "Не удалось убрать адрес");
  };
  const locateAll = () =>
    run("locate", async () => {
      let last = await locateTripStops(trip.id);
      let found = last.found;
      setTrip(last.trip);
      for (let round = 0; round < 12 && last.remaining > 0; round += 1) {
        last = await locateTripStops(trip.id);
        found += last.found;
        setTrip(last.trip);
      }
      const rest = last.trip.stops.filter((s) => !hasPoint(s)).length;
      toast.info(rest ? `Нашёл ${found}, без точки ${rest}` : "Все адреса на карте", rest ? { description: "Точку поставит телефон на месте — «Я здесь»" } : {});
    }, "Не удалось найти координаты");
  const placeHere = (point: Point) => {
    const target = placing;
    if (target == null) return;
    setPlacing(null);
    void run("place", async () => patchStop(await updateTripStop(target, point)), "Не удалось поставить точку");
  };
  const remove = async () => {
    if (!(await confirmDialog({ title: "Удалить командировку?", text: "Адреса и пустые наряды удалятся. С внесёнными поверками — только завершить.", ok: "Удалить", danger: true }))) return;
    void run("delete", async () => { await deleteTrip(trip.id); onBack(); }, "Не удалось удалить");
  };

  const stats = (
    <div className="trip-stats">
      <div className="trip-stat"><b>{summary.stops_done}<small> из {summary.stops_total}</small></b><span>адресов выполнено</span></div>
      <div className="trip-stat">
        <b>{summary.instruments_entered}{summary.instruments_planned > 0 && <small> из {summary.instruments_planned}</small>}</b>
        <span>приборов: факт / план{summary.instruments_unsuitable > 0 ? ` · ${summary.instruments_unsuitable} непригодн.` : ""}</span>
      </div>
      <div className="trip-stat"><b>{missed}</b><span>не состоялось{missed ? ` · не открыли ${summary.stops_no_answer}` : ""}</span></div>
      <div className="trip-stat"><b>{summary.stops_on_site}</b><span>добавлено на месте</span></div>
    </div>
  );

  const list = (
    <div className="trip-list">
      {shown.length === 0 ? (
        <EmptyState compact icon="mapPin" title={day === "all" ? "Адресов пока нет" : "На этот день адресов нет"}
          action={canEdit && open ? { label: "Добавить адрес", icon: "plus", onClick: () => setAdding("one") } : undefined}>
          {day === "all" && open ? "Добавьте адреса из плана — по одному или списком; тех, кто появится на месте, — кнопкой «Я здесь»." : undefined}
        </EmptyState>
      ) : (
        <div className="card-list">
          {shown.map((stop) => (
            <div key={stop.id} className="trips-stop" style={{ "--day": colorOf(stop) ?? "transparent" } as CSSProperties}>
              <StopRow stop={stop} me={myPoint} active={stop.id === openStop} onOpen={() => setOpenStop(stop.id)} showDay={day === "all" && days.length > 1} />
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className={layout === "map" ? "trip-wide trips-view" : "narrow trips-view"}>
      <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Командировки</button>
      <OfflineBadge stale={stale} />

      <header className="trips-head">
        <div className="grow">
          <div className="sub">
            {[dateRange(trip.date_start, trip.date_end), days.length > 1 ? `${days.length} дн.` : "", trip.visit_number ? `заход ${trip.visit_number}` : ""].filter(Boolean).join(" · ")}
          </div>
          <h2>{trip.destination && trip.destination !== trip.title ? <>{trip.destination} <small>{trip.title}</small></> : trip.title}</h2>
          <div className="trip-tags">
            <span className={`pill ${TRIP_TONE[trip.status]}`}>{trip.status_display}</span>
            <span className="pill muted plain" title={people.map((p) => p.full_name).join(", ")}><Icon name="users" size={12} /> {people.map((p) => p.full_name.split(" ")[0]).join(", ")}</span>
            {trip.procurement && (
              <button type="button" className="pill purple" onClick={() => go("procurements", { action: "open-procurement", payload: trip.procurement!.id })}>
                <Icon name="contract" size={12} /> Закупка {trip.procurement.label} <Icon name="arrowUpRight" size={11} />
              </button>
            )}
            {trip.procurement && <DeadlinePill deadline={trip.procurement.deadline} closed={!open} />}
            {trip.passes.map((p) => (
              <span key={p.employee_id} className={`pill ${p.state === "ok" ? "ok" : p.state === "pending" ? "warn" : "bad"}`} title={p.text}>
                пропуск {p.full_name.split(" ")[0]}: {p.text}
              </span>
            ))}
          </div>
        </div>
        <div className="trips-head-actions">
          {canEdit && trip.status === "planned" && <button type="button" className="primary sm" disabled={!!busy} onClick={() => void setStatus("active")}>Начать</button>}
          {canEdit && trip.status === "active" && <button type="button" className="sm" disabled={!!busy} onClick={() => void finish()}>Завершить</button>}
          {canEdit && !open && <button type="button" className="sm" disabled={!!busy} onClick={() => void setStatus("active")}><Icon name="undo" size={14} /> Вернуть в работу</button>}
          {canEdit && <button type="button" className="icon-btn" aria-label="Изменить командировку" onClick={() => setEditing(true)}><Icon name="pen" size={17} /></button>}
        </div>
      </header>
      {trip.note && <p className="trip-note">{trip.note}</p>}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {stats}

      {nextStop && open && (
        <section className="trips-next">
          <span className="city-next-label">Следующий заход{nextStop.planned_date && days.length > 1 ? ` · ${dayTab(nextStop.planned_date)}` : ""}</span>
          <button type="button" className="city-next-address" onClick={() => setOpenStop(nextStop.id)}>
            <b>{nextStop.address}</b>
            <span>{[nextStop.client !== "Физическое лицо" ? nextStop.client : "", nextStop.contact_name, nextStop.planned_count ? `ждут ${instruments(nextStop.planned_count)}` : ""].filter(Boolean).join(" · ")}</span>
          </button>
          <div className="city-big-actions">
            {nextStop.contact_phone && <a className="btn big" href={`tel:${nextStop.contact_phone.replace(/[^\d+]/g, "")}`}><Icon name="phone" /> Позвонить</a>}
            <button type="button" className="big" onClick={() => openNavigator(nextStop)}><Icon name="route" /> Навигатор</button>
            <button type="button" className="primary big" disabled={nextStop.work_order_id == null} onClick={() => setWorking(nextStop.id)}><Icon name="gauge" /> Поверять</button>
          </div>
        </section>
      )}

      {canEdit && open && (
        <div className="trip-add">
          <button type="button" className="primary big" onClick={() => setAdding("one")} title="Адрес одним экраном: точка — с телефона"><Icon name="mapPin" /> Я здесь — адрес</button>
          <button type="button" className="big" onClick={() => setAdding("bulk")} title="Строка = адрес; ФИО; телефон; сколько приборов"><Icon name="list" /> Списком</button>
        </div>
      )}

      {stops.length > 0 && (
        <div className="toolbar trip-toolbar">
          <Segmented value={layout} onChange={setLayout} options={[{ value: "list", label: "Список", icon: "list" }, { value: "map", label: "Карта", icon: "map" }]} />
          <span className="spacer" />
          {canEdit && open && summary.stops_waiting > 0 && (
            <button type="button" className="sm tinted" disabled={!!busy} onClick={() => setPlanning(true)} title="Раскладка ждущих адресов по дням: рабочий день, окна, время в пути">
              <Icon name="bolt" size={14} /> {days.length > 1 ? "Разложить по дням" : "Маршрут дня"}
            </button>
          )}
          {waitingPoints.length > 0 && (
            <a className="btn sm" href={routeUrl(waitingPoints.slice(0, 14), myPoint)} target="_blank" rel="noreferrer"><Icon name="external" size={14} /> В навигатор</a>
          )}
        </div>
      )}
      {tabs.length > 2 && (
        <div className="trips-days" role="tablist" aria-label="Дни поездки">
          {tabs.map((t, i) => (
            <button type="button" role="tab" key={t.value} aria-selected={day === t.value}
              className={`trips-day ${day === t.value ? "active" : ""} ${t.value === today() ? "is-today" : ""}`}
              style={{ "--day": i > 0 && t.value !== NO_DAY ? DAY_COLORS[(i - 1) % DAY_COLORS.length] : "var(--graphite)" } as CSSProperties}
              onClick={() => setDay(t.value)}>
              <i /> {t.label} <small className="data">{t.count}</small>
            </button>
          ))}
        </div>
      )}

      {trip.route && summary.stops_waiting > 0 && (
        <RouteCard
          route={trip.route}
          canEdit={canEdit}
          busy={!!busy}
          onReplan={() => setPlanning(true)}
          onFromMe={() =>
            void run("plan", async () => {
              const point = myPoint ?? (await locateMe(8000));
              setMyPoint(point);
              const result = await planTripRoute(trip.id, { ...planDefaults(trip), start: "me", latitude: point.latitude, longitude: point.longitude, departure: new Date().toISOString(), apply: true });
              if (result.trip) setTrip(result.trip);
              toast.success("Пересчитано от вашего места");
            }, "Не удалось пересчитать маршрут")
          }
        />
      )}
      {unlocated > 0 && (
        <div className="trip-unlocated">
          <Icon name="mapPin" size={15} /><span>{addresses(unlocated)} без точки на карте</span>
          {canEdit && trip.can_locate && <button type="button" className="sm" disabled={!!busy} onClick={() => void locateAll()}>{busy === "locate" ? "Ищу…" : "Найти по адресу"}</button>}
        </div>
      )}

      {layout === "map" && stops.length > 0 ? (
        <div className="trip-split">
          <div className="trip-map-wrap">
            {placing != null && (
              <div className="trip-placing">Коснитесь карты, чтобы поставить точку <button type="button" className="sm" onClick={() => setPlacing(null)}>Отмена</button></div>
            )}
            <Suspense fallback={<SkeletonList rows={2} label="Загружаю карту…" />}>
              <TripMap key={days.join(",")} stops={shown} selectedId={openStop} me={myPoint} colorOf={colorOf} onSelect={(s) => setOpenStop(s.id)} onPlace={placing != null ? placeHere : null} />
            </Suspense>
            {days.length > 1 && (
              <div className="trips-legend glass">
                {days.map((d, i) => <span key={d} style={{ "--day": DAY_COLORS[i % DAY_COLORS.length] } as CSSProperties}><i /> {dayTab(d)}</span>)}
              </div>
            )}
          </div>
          {list}
        </div>
      ) : list}

      {canEdit && trip.procurement && !open && left > 0 && (
        <>
          <button type="button" className="big wide" onClick={() => setNextVisit(true)}><Icon name="arrowRight" /> Следующий заход</button>
          <p className="hint trip-foot">Неотработанные адреса ({left}) переедут в новый заход вместе с заметками и контактами.</p>
        </>
      )}

      {selected && (
        <StopSheet
          stop={selected}
          trip={trip}
          canEdit={canEdit}
          busy={!!busy}
          index={stops.findIndex((s) => s.id === selected.id)}
          total={stops.length}
          onClose={() => setOpenStop(null)}
          onWork={() => { setOpenStop(null); setWorking(selected.id); }}
          onMark={(status) => void markStop(selected, status, patchStop, () => void reload())}
          onMove={(delta) => move(selected, delta)}
          onRemove={() => void removeStop(selected)}
          onSaved={patchStop}
          onPlaceOnMap={() => { setPlacing(selected.id); setOpenStop(null); setLayout("map"); }}
          onError={setError}
        />
      )}
      {adding === "one" && (
        <AddStopModal trip={trip} procurement={null} onClose={() => setAdding(null)}
          onAdded={(stop, work) => {
            setAdding(null);
            toast.success("Адрес добавлен", { description: stop.address });
            void reload().then(() => work && setWorking(stop.id));
          }} />
      )}
      {adding === "bulk" && (
        <BulkModal trip={trip} onClose={() => setAdding(null)}
          onAdded={(fresh, text) => {
            setAdding(null);
            setTrip(fresh);
            toast.success(text);
            if (fresh.can_locate && fresh.stops.some((s) => !hasPoint(s))) void locateAll();
          }} />
      )}
      {editing && (
        <EditTripModal trip={trip} isManager={isManager} onClose={() => setEditing(false)}
          onSaved={(fresh) => { setEditing(false); setTrip(fresh); }}
          onDelete={() => { setEditing(false); void remove(); }} />
      )}
      {planning && (
        <PlanModal trip={trip} me={myPoint} onClose={() => setPlanning(false)}
          onApplied={(fresh, text) => { setPlanning(false); setTrip(fresh); toast.success("Раскладка сохранена", { description: text }); }} />
      )}
      {nextVisit && (
        <NextVisitModal trip={trip} onClose={() => setNextVisit(false)}
          onCreated={(nid) => { setNextVisit(false); toast.success("Следующий заход заведён"); onOpen(nid); }} />
      )}
    </div>
  );
}
