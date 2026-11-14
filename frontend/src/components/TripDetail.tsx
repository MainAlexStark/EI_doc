import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addTripStop,
  addTripStopsBulk,
  deleteTrip,
  deleteTripStop,
  fetchEmployees,
  fetchProcurement,
  fetchTrip,
  locateTripStops,
  reorderTripStops,
  startNextVisit,
  suggestAddress,
  updateTrip,
  updateTripStop,
  type AddressSuggestion,
  type Employee,
  type Me,
  type Procurement,
  type StopPayload,
  type StopStatus,
  type Trip,
  type TripStop,
} from "../api";
import Icon, { type IconName } from "../ui/Icon";
import { Modal, confirmDialog } from "../ui/Modal";
import { Alert, EmptyState, Segmented, Spinner, Switch } from "../ui/controls";
import { usePersistent } from "../ui/hooks";
import FieldWork from "./FieldWork";
import { DeadlinePill } from "./TripBits";
import {
  STOP_TONE,
  TRIP_TONE,
  addresses,
  dateRange,
  distanceKm,
  errorText,
  hasPoint,
  instruments,
  locateMe,
  nearestOrder,
  routeUrl,
  ruDate,
  searchUrl,
  telHref,
  today,
  type Point,
} from "./tripUtil";

const TripMap = lazy(() => import("./TripMap"));

/** Одна командировка: сводка план/факт, адреса списком и на карте.
 *
 * Адрес — это наряд, поэтому «Поверять приборы» открывает тот же экран, что и
 * вкладка «Поверки» (FieldWork), уже на нужном наряде: офлайн-очередь, фото,
 * бланк для режимного объекта — всё работает как в городе. */

type Filter = "all" | "planned" | "done" | "missed";

const MARKS: { status: StopStatus; label: string; icon: IconName }[] = [
  { status: "done", label: "Выполнен", icon: "checkCircle" },
  { status: "no_answer", label: "Не открыли", icon: "lock" },
  { status: "postponed", label: "Перенос", icon: "clock" },
  { status: "refused", label: "Отказ", icon: "ban" },
];

export default function TripDetail({
  tripId, me, isManager, onBack, onOpenProcurement, onOpenTrip,
}: {
  tripId: number;
  me: Me;
  isManager: boolean;
  onBack: () => void;
  onOpenProcurement: (id: number) => void;
  onOpenTrip: (id: number) => void;
}) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [procurement, setProcurement] = useState<Procurement | null>(null);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [mode, setMode] = usePersistent<"list" | "map">("ei_doc_trip_mode", "list", ["list", "map"]);
  const [filter, setFilter] = useState<Filter>("all");
  const [openStop, setOpenStop] = useState<number | null>(null);
  const [working, setWorking] = useState<number | null>(null); // адрес, по которому идёт поверка
  const [adding, setAdding] = useState<null | "one" | "bulk">(null);
  const [editing, setEditing] = useState(false);
  const [nextVisit, setNextVisit] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [myPoint, setMyPoint] = useState<Point | null>(null);
  const [placing, setPlacing] = useState<number | null>(null); // адрес, которому ставят точку касанием карты

  const reload = useCallback(async () => {
    try {
      const fresh = await fetchTrip(tripId);
      setTrip(fresh);
      setError("");
      return fresh;
    } catch (exc) {
      const status = (exc as { status?: number }).status;
      if (status === 404) setMissing(true);
      else setError(errorText(exc, "Не удалось загрузить командировку"));
      return null;
    }
  }, [tripId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const procurementId = trip?.procurement?.id ?? null;
  useEffect(() => {
    if (procurementId == null) return;
    fetchProcurement(procurementId).then(setProcurement).catch(() => undefined);
  }, [procurementId]);

  // Моё место на карте — пока карта открыта.
  useEffect(() => {
    if (mode !== "map" || !("geolocation" in navigator)) return;
    const watch = navigator.geolocation.watchPosition(
      (position) => setMyPoint({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 20_000 },
    );
    return () => navigator.geolocation.clearWatch(watch);
  }, [mode]);

  const patchStop = useCallback((fresh: TripStop) => {
    setTrip((current) =>
      current ? { ...current, stops: current.stops.map((stop) => (stop.id === fresh.id ? fresh : stop)) } : current,
    );
  }, []);

  const stops = trip?.stops ?? [];
  const shown = useMemo(
    () =>
      stops.filter((stop) =>
        filter === "all"
          ? true
          : filter === "planned"
            ? stop.status === "planned"
            : filter === "done"
              ? stop.status === "done"
              : stop.status !== "planned" && stop.status !== "done",
      ),
    [stops, filter],
  );

  if (missing) {
    return (
      <div className="narrow">
        <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Командировки</button>
        <EmptyState icon="car" title="Командировка не найдена">Возможно, её удалили или вы в ней не участвуете.</EmptyState>
      </div>
    );
  }
  if (!trip) {
    return (
      <div className="narrow">
        <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Командировки</button>
        {error ? <Alert>{error}</Alert> : <Spinner />}
      </div>
    );
  }

  // --- Поверка по адресу: экран «Поверок» на наряде этого адреса ---------
  const workingStop = stops.find((stop) => stop.id === working);
  if (workingStop?.work_order_id != null) {
    return (
      <FieldWork
        me={me}
        isManager={isManager}
        openOrderId={workingStop.work_order_id}
        backLabel={trip.title}
        fallbackTitle={workingStop.address}
        onClose={() => {
          setWorking(null);
          void reload();
          window.scrollTo({ top: 0 });
        }}
      />
    );
  }

  const canEdit = trip.can_edit;
  const open = trip.status === "planned" || trip.status === "active";
  const summary = trip.summary;
  const unlocated = stops.filter((stop) => !hasPoint(stop)).length;
  const waitingPoints = stops.filter((stop) => stop.status === "planned" && hasPoint(stop)) as (TripStop & Point)[];
  const selected = stops.find((stop) => stop.id === openStop) ?? null;
  const people = [trip.lead, ...trip.members];

  const run = async (label: string, action: () => Promise<unknown>, failure: string) => {
    setBusy(label);
    setError("");
    try {
      await action();
    } catch (exc) {
      setError(errorText(exc, failure));
    } finally {
      setBusy("");
    }
  };

  const setStatus = (status: Trip["status"]) =>
    run("status", async () => setTrip(await updateTrip(trip.id, { status })), "Не удалось сменить состояние");

  const finish = async () => {
    const left = summary.stops_waiting;
    const ok = await confirmDialog({
      title: "Завершить командировку?",
      text: left
        ? `${addresses(left)} ещё ждут. Они останутся в этой командировке неотработанными${trip.procurement ? " — в следующий заход их можно перенести одной кнопкой" : ""}.`
        : "Все адреса отмечены. Новые адреса добавить будет нельзя.",
      ok: "Завершить",
    });
    if (ok) void setStatus("done");
  };

  const remove = async () => {
    const ok = await confirmDialog({
      title: "Удалить командировку?",
      text: "Адреса и пустые наряды под них удалятся. Если поверки уже внесены, удалить не получится — только завершить.",
      ok: "Удалить",
      danger: true,
    });
    if (!ok) return;
    void run("delete", async () => {
      await deleteTrip(trip.id);
      onBack();
    }, "Не удалось удалить");
  };

  const mark = (stop: TripStop, status: StopStatus) =>
    run(`mark-${stop.id}`, async () => {
      patchStop(await updateTripStop(stop.id, { status }));
      await reload();
    }, "Не удалось поставить отметку");

  const move = (stop: TripStop, delta: number) => {
    const ids = stops.map((item) => item.id);
    const index = ids.indexOf(stop.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    void run("order", async () => setTrip(await reorderTripStops(trip.id, ids)), "Не удалось поменять порядок");
  };

  const sortNearest = () =>
    run("order", async () => {
      let from: Point | null = myPoint;
      if (!from) {
        try {
          from = await locateMe(6000);
          setMyPoint(from);
        } catch {
          from = null; // без моего места начинаем с первого адреса
        }
      }
      setTrip(await reorderTripStops(trip.id, nearestOrder(stops, from)));
      setNotice(from ? "Адреса выстроены от вас к ближайшему и дальше по цепочке" : "Адреса выстроены по цепочке от первого");
    }, "Не удалось упорядочить");

  const locateAll = (prefix = "") =>
    run("locate", async () => {
      let found = 0;
      let last = await locateTripStops(trip.id);
      found += last.found;
      setTrip(last.trip);
      // Сервер ищет порциями (не чаще запроса в секунду) — повторяем, пока есть что искать.
      for (let round = 0; round < 12 && last.remaining > 0; round += 1) {
        last = await locateTripStops(trip.id);
        found += last.found;
        setTrip(last.trip);
      }
      const left = last.trip.stops.filter((stop) => !hasPoint(stop)).length;
      setNotice(
        [
          prefix,
          left
            ? `На карте нашёл ${found}, без точки осталось ${left} — откройте адрес и поставьте точку на карте или кнопкой «Я здесь».`
            : "Все адреса на карте.",
        ].filter(Boolean).join(" "),
      );
    }, "Не удалось найти координаты");

  const placeHere = (point: Point) => {
    const target = placing;
    if (target == null) return;
    setPlacing(null);
    void run("place", async () => patchStop(await updateTripStop(target, point)), "Не удалось поставить точку");
  };

  const removeStop = async (stop: TripStop) => {
    const ok = await confirmDialog({ title: "Убрать адрес?", text: stop.address, ok: "Убрать", danger: true });
    if (!ok) return;
    void run("remove", async () => {
      await deleteTripStop(stop.id);
      setOpenStop(null);
      await reload();
    }, "Не удалось убрать адрес");
  };

  const counts: Record<Filter, number> = {
    all: stops.length,
    planned: summary.stops_waiting,
    done: summary.stops_done,
    missed: summary.stops_no_answer + summary.stops_postponed + summary.stops_refused,
  };

  return (
    <div className={mode === "map" ? "trip-wide" : "narrow"}>
      <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Командировки</button>

      <div className="detail-title">
        <div className="sub">
          {[
            dateRange(trip.date_start, trip.date_end),
            trip.destination && trip.destination !== trip.title ? trip.destination : "",
            trip.visit_number ? `заход ${trip.visit_number}` : "",
          ].filter(Boolean).join(" · ")}
        </div>
        <h2>{trip.title}</h2>
      </div>

      <div className="trip-headline">
        <span className={`pill ${TRIP_TONE[trip.status]}`}>{trip.status_display}</span>
        <span className="trip-people" title={people.map((p) => p.full_name).join(", ")}>
          <Icon name="users" size={15} /> {people.map((p) => p.full_name).join(", ")}
        </span>
        <span className="spacer" />
        {canEdit && trip.status === "planned" && (
          <button type="button" className="primary sm" disabled={!!busy} onClick={() => void setStatus("active")}>Начать</button>
        )}
        {canEdit && trip.status === "active" && (
          <button type="button" className="sm" disabled={!!busy} onClick={() => void finish()}>Завершить</button>
        )}
        {canEdit && !open && (
          <button type="button" className="sm" disabled={!!busy} onClick={() => void setStatus("active")}>
            <Icon name="undo" size={14} /> Вернуть в работу
          </button>
        )}
        {canEdit && (
          <button type="button" className="icon-btn" aria-label="Изменить командировку" onClick={() => setEditing(true)}>
            <Icon name="pen" size={17} />
          </button>
        )}
      </div>

      {trip.note && <p className="trip-note">{trip.note}</p>}

      <div className="trip-stats">
        <div className="trip-stat">
          <b>{summary.stops_done}<small> из {summary.stops_total}</small></b>
          <span>адресов выполнено</span>
        </div>
        <div className="trip-stat">
          <b>{summary.instruments_entered}{summary.instruments_planned > 0 && <small> из {summary.instruments_planned}</small>}</b>
          <span>приборов внесено{summary.instruments_unsuitable > 0 ? ` · ${summary.instruments_unsuitable} непригодн.` : ""}</span>
        </div>
        <div className="trip-stat">
          <b>{counts.missed}</b>
          <span>не состоялось{counts.missed > 0 ? ` · не открыли ${summary.stops_no_answer}` : ""}</span>
        </div>
        <div className="trip-stat">
          <b>{summary.stops_on_site}</b>
          <span>добавлено на месте</span>
        </div>
      </div>

      {trip.procurement && (
        <button type="button" className="card tap trip-contract" onClick={() => onOpenProcurement(trip.procurement!.id)}>
          <div className="order-row">
            <div className="grow">
              <div className="sub">Закупка {trip.procurement.label} · {trip.procurement.law_display}</div>
              <b>{trip.procurement.customer}</b>
              <div className="sub">
                {[
                  trip.procurement.contract_number && `контракт № ${trip.procurement.contract_number}`,
                  procurement && procurement.progress.planned > 0 &&
                    `по контракту поверено ${procurement.progress.ready} из ${procurement.progress.planned}`,
                  procurement && procurement.documents.length > 0 && `документов: ${procurement.documents.length}`,
                ].filter(Boolean).join(" · ") || trip.procurement.title}
              </div>
              <div className="trip-tags">
                <DeadlinePill deadline={trip.procurement.deadline} closed={trip.procurement.status !== "active"} />
                {trip.passes.map((item) => (
                  <span
                    key={item.employee_id}
                    className={`pill ${item.state === "ok" ? "ok" : item.state === "pending" ? "warn" : "bad"}`}
                    title={item.text}
                  >
                    {item.full_name.split(" ")[0]}: {item.text}
                  </span>
                ))}
              </div>
            </div>
            <Icon name="chevronRight" size={18} />
          </div>
        </button>
      )}

      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {notice && <Alert tone="ok" onClose={() => setNotice("")}>{notice}</Alert>}

      {canEdit && open && (
        <div className="trip-add">
          <button type="button" className="primary big" onClick={() => setAdding("one")}>
            <Icon name="plus" /> Добавить адрес
          </button>
          <button type="button" className="big" onClick={() => setAdding("bulk")} title="Несколько адресов сразу, по одному в строке">
            <Icon name="list" /> Списком
          </button>
        </div>
      )}

      {stops.length > 0 && (
        <div className="toolbar trip-toolbar">
          <Segmented
            value={mode}
            options={[
              { value: "list", label: "Список", icon: "list" },
              { value: "map", label: "Карта", icon: "mapPin" },
            ]}
            onChange={setMode}
          />
          <span className="spacer" />
          {canEdit && waitingPoints.length > 1 && (
            <button type="button" className="sm" disabled={!!busy} onClick={() => void sortNearest()} title="Выстроить ждущие адреса по цепочке: каждый раз к ближайшему">
              <Icon name="bolt" size={14} /> {busy === "order" ? "Строю…" : "По близости"}
            </button>
          )}
          {waitingPoints.length > 0 && (
            <a className="btn sm" href={routeUrl(waitingPoints.slice(0, 14), myPoint)} target="_blank" rel="noreferrer">
              <Icon name="external" size={14} /> Маршрут
            </a>
          )}
        </div>
      )}

      {stops.length > 0 && unlocated > 0 && (
        <div className="trip-unlocated">
          <Icon name="mapPin" size={15} />
          <span>{addresses(unlocated)} без точки на карте</span>
          {canEdit && trip.can_locate && (
            <button type="button" className="sm" disabled={!!busy} onClick={() => void locateAll()}>
              {busy === "locate" ? "Ищу…" : "Найти по адресу"}
            </button>
          )}
        </div>
      )}

      {stops.length === 0 && (
        <EmptyState icon="mapPin" title="Адресов пока нет">
          {open
            ? "Добавьте адреса из плана — по одному или списком. Тех, кто появится на месте, добавите уже там, в два касания."
            : "В этой командировке адресов не было."}
        </EmptyState>
      )}

      <div className={mode === "map" ? "trip-split" : ""}>
        {mode === "map" && stops.length > 0 && (
          <div className="trip-map-wrap">
            {placing != null && (
              <div className="trip-placing">
                Коснитесь карты, чтобы поставить точку
                <button type="button" className="sm" onClick={() => setPlacing(null)}>Отмена</button>
              </div>
            )}
            <Suspense fallback={<Spinner label="Загружаю карту…" />}>
              <TripMap
                stops={stops}
                selectedId={openStop}
                me={myPoint}
                onSelect={(stop) => setOpenStop(stop.id)}
                onPlace={placing != null ? placeHere : null}
              />
            </Suspense>
          </div>
        )}

        {stops.length > 0 && (
          <div className="trip-list">
            <Segmented
              size="sm"
              className="trip-filter"
              value={filter}
              options={[
                { value: "all", label: "Все", count: counts.all },
                { value: "planned", label: "Ждут", count: counts.planned },
                { value: "done", label: "Выполнены", count: counts.done },
                { value: "missed", label: "Не состоялись", count: counts.missed },
              ]}
              onChange={setFilter}
            />
            {shown.length === 0 && <div className="empty small">Таких адресов нет</div>}
            <div className="card-list">
              {shown.map((stop) => (
                <StopRow key={stop.id} stop={stop} me={myPoint} active={stop.id === openStop} onOpen={() => setOpenStop(stop.id)} />
              ))}
            </div>
          </div>
        )}
      </div>

      {canEdit && trip.procurement && (summary.stops_waiting + summary.stops_no_answer + summary.stops_postponed > 0) && !open && (
        <button type="button" className="big wide" onClick={() => setNextVisit(true)}>
          <Icon name="arrowRight" /> Следующий заход
        </button>
      )}
      {canEdit && trip.procurement && !open && summary.stops_waiting + summary.stops_no_answer + summary.stops_postponed > 0 && (
        <p className="hint trip-foot">Неотработанные адреса переедут в новый заход вместе с заметками и контактами.</p>
      )}
      {canEdit && trip.procurement && open && stops.length > 0 && (
        <p className="hint trip-foot">
          Не успели всё за этот приезд? Завершите командировку — неотработанные адреса переедут в следующий заход одной кнопкой.
        </p>
      )}

      {selected && (
        <StopSheet
          stop={selected}
          trip={trip}
          canEdit={canEdit}
          busy={!!busy}
          index={stops.findIndex((stop) => stop.id === selected.id)}
          total={stops.length}
          onClose={() => setOpenStop(null)}
          onWork={() => {
            setOpenStop(null);
            setWorking(selected.id);
          }}
          onMark={(status) => void mark(selected, status)}
          onMove={(delta) => move(selected, delta)}
          onRemove={() => void removeStop(selected)}
          onSaved={(fresh) => patchStop(fresh)}
          onPlaceOnMap={() => {
            setPlacing(selected.id);
            setOpenStop(null);
            setMode("map");
          }}
          onError={setError}
        />
      )}

      {adding === "one" && (
        <AddStopModal
          trip={trip}
          procurement={procurement}
          onClose={() => setAdding(null)}
          onAdded={(stop, work) => {
            setAdding(null);
            void reload().then(() => {
              if (work) setWorking(stop.id);
            });
          }}
        />
      )}
      {adding === "bulk" && (
        <BulkModal
          trip={trip}
          onClose={() => setAdding(null)}
          onAdded={(fresh, text) => {
            setAdding(null);
            setTrip(fresh);
            setNotice(text);
            // Адреса из списка набраны текстом — сразу ищем им место на карте.
            if (fresh.can_locate && fresh.stops.some((stop) => !hasPoint(stop))) void locateAll(text);
          }}
        />
      )}
      {editing && (
        <EditTripModal
          trip={trip}
          isManager={isManager}
          onClose={() => setEditing(false)}
          onSaved={(fresh) => {
            setEditing(false);
            setTrip(fresh);
          }}
          onDelete={() => {
            setEditing(false);
            void remove();
          }}
        />
      )}
      {nextVisit && (
        <NextVisitModal
          trip={trip}
          onClose={() => setNextVisit(false)}
          onCreated={(id) => {
            setNextVisit(false);
            onOpenTrip(id);
          }}
        />
      )}
    </div>
  );
}

function StopRow({ stop, me, active, onOpen }: { stop: TripStop; me: Point | null; active: boolean; onOpen: () => void }) {
  const who = [stop.client !== "Физическое лицо" ? stop.client : "", stop.contact_name !== stop.client ? stop.contact_name : ""]
    .filter(Boolean)
    .join(", ");
  const away = me && hasPoint(stop) && stop.status === "planned" ? distanceKm(me, stop) : null;
  const line = [
    who,
    stop.contact_phone,
    away != null ? (away < 1 ? `${Math.round(away * 1000)} м` : `${away.toFixed(1)} км`) : "",
    stop.note,
  ].filter(Boolean);
  return (
    <button type="button" className={`card tap trip-stop ${active ? "active" : ""}`} onClick={onOpen}>
      <span className={`trip-pin ${stop.status}`}>{stop.position}</span>
      <div className="grow">
        <b>{stop.address}</b>
        {line.length > 0 && <div className="sub">{line.join(" · ")}</div>}
        <div className="trip-tags">
          {stop.status !== "planned" && <span className={`pill ${STOP_TONE[stop.status]}`}>{stop.status_display}</span>}
          {stop.added_on_site && <span className="pill info plain">на месте</span>}
          {stop.is_restricted && <span className="pill warn plain">режимный</span>}
          {stop.carried_from && <span className="pill purple plain">перенесён</span>}
          {!hasPoint(stop) && <span className="pill muted plain">нет точки</span>}
        </div>
      </div>
      <div className="order-progress">
        {stop.verifications_total > 0
          ? instruments(stop.verifications_total)
          : stop.planned_count
            ? `ждём ${stop.planned_count}`
            : ""}
        {stop.verifications_total > 0 && stop.verifications_ready < stop.verifications_total && (
          <small>без измерений: {stop.verifications_total - stop.verifications_ready}</small>
        )}
      </div>
    </button>
  );
}

function StopSheet({
  stop, trip, canEdit, busy, index, total, onClose, onWork, onMark, onMove, onRemove, onSaved, onPlaceOnMap, onError,
}: {
  stop: TripStop;
  trip: Trip;
  canEdit: boolean;
  busy: boolean;
  index: number;
  total: number;
  onClose: () => void;
  onWork: () => void;
  onMark: (status: StopStatus) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
  onSaved: (stop: TripStop) => void;
  onPlaceOnMap: () => void;
  onError: (text: string) => void;
}) {
  const [note, setNote] = useState(stop.note);
  const [count, setCount] = useState(stop.planned_count != null ? String(stop.planned_count) : "");
  const [phone, setPhone] = useState(stop.contact_phone);
  const [name, setName] = useState(stop.contact_name);
  const [saving, setSaving] = useState("");
  const [local, setLocal] = useState("");
  const open = trip.status === "planned" || trip.status === "active";

  const dirty =
    note !== stop.note || phone !== stop.contact_phone || name !== stop.contact_name ||
    count !== (stop.planned_count != null ? String(stop.planned_count) : "");

  const save = async (payload: StopPayload, label: string) => {
    setSaving(label);
    setLocal("");
    try {
      onSaved(await updateTripStop(stop.id, payload));
    } catch (exc) {
      setLocal(errorText(exc, "Не удалось сохранить"));
    } finally {
      setSaving("");
    }
  };

  const here = async () => {
    setSaving("here");
    setLocal("");
    try {
      const point = await locateMe();
      onSaved(await updateTripStop(stop.id, point));
    } catch (exc) {
      setLocal(errorText(exc, "Не удалось определить местоположение"));
    } finally {
      setSaving("");
    }
  };

  const who = [stop.client, stop.contact_name !== stop.client ? stop.contact_name : ""].filter(Boolean).join(" · ");

  return (
    <Modal
      open
      onClose={onClose}
      title={<><span className={`trip-pin ${stop.status}`}>{stop.position}</span> {stop.address}</>}
      subtitle={who}
    >
      <div className="form-stack">
        <div className="trip-tags">
          <span className={`pill ${STOP_TONE[stop.status]}`}>{stop.status_display}</span>
          {stop.verifications_total > 0 && <span className="pill ok plain">внесено: {instruments(stop.verifications_total)}</span>}
          {stop.verifications_unsuitable > 0 && <span className="pill bad plain">непригодных: {stop.verifications_unsuitable}</span>}
          {stop.added_on_site && <span className="pill info plain">добавлен на месте</span>}
          {stop.carried_from && <span className="pill purple plain">из «{stop.carried_from}»</span>}
        </div>

        {stop.is_restricted && (
          <Alert tone="warn">
            Режимный объект{stop.access_note ? `: ${stop.access_note}` : ""}. Если телефон нельзя — в поверке есть бумажный бланк.
          </Alert>
        )}

        <button type="button" className="primary big" disabled={stop.work_order_id == null} onClick={onWork}>
          <Icon name="gauge" /> {stop.verifications_total > 0 ? "Приборы и измерения" : "Поверять приборы"}
        </button>

        <div className="trip-quick">
          {hasPoint(stop) ? (
            <a className="btn" href={routeUrl([stop])} target="_blank" rel="noreferrer"><Icon name="mapPin" size={16} /> Маршрут</a>
          ) : (
            <a className="btn" href={searchUrl(stop.address)} target="_blank" rel="noreferrer"><Icon name="search" size={16} /> На карте</a>
          )}
          {stop.contact_phone && (
            <a className="btn" href={telHref(stop.contact_phone)}><Icon name="phone" size={16} /> Позвонить</a>
          )}
        </div>

        {canEdit && open && (
          <div>
            <span className="field-label">Отметка</span>
            <div className="trip-marks">
              {MARKS.map((item) => (
                <button
                  type="button"
                  key={item.status}
                  className={`trip-mark ${item.status} ${stop.status === item.status ? "active" : ""}`}
                  disabled={busy}
                  aria-pressed={stop.status === item.status}
                  onClick={() => onMark(stop.status === item.status ? "planned" : item.status)}
                >
                  <Icon name={item.icon} size={18} />
                  {item.label}
                </button>
              ))}
            </div>
            {stop.status !== "planned" && <div className="hint">Нажмите отметку ещё раз, чтобы вернуть адрес в «ждёт».</div>}
          </div>
        )}

        {canEdit && (
          <>
            <div className="field-row">
              <div className="field">
                <label htmlFor="stop-name">Кто встречает</label>
                <input id="stop-name" value={name} onChange={(event) => setName(event.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="stop-phone">Телефон</label>
                <input id="stop-phone" type="tel" inputMode="tel" value={phone} onChange={(event) => setPhone(event.target.value)} />
              </div>
            </div>
            <div className="field-row">
              <div className="field" style={{ flex: "0 0 130px" }}>
                <label htmlFor="stop-count">Ждём приборов</label>
                <input id="stop-count" inputMode="numeric" value={count} onChange={(event) => setCount(event.target.value.replace(/\D/g, ""))} />
              </div>
              <div className="field">
                <label htmlFor="stop-note">Заметка</label>
                <input id="stop-note" value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} placeholder="Домофон, собака, после 18:00…" />
              </div>
            </div>
            {dirty && (
              <button
                type="button"
                className="primary"
                disabled={!!saving}
                onClick={() =>
                  void save({ note, contact_phone: phone, contact_name: name, planned_count: count ? Number(count) : null }, "fields")
                }
              >
                {saving === "fields" ? "Сохраняю…" : "Сохранить"}
              </button>
            )}

            <div className="trip-sheet-tools">
              <button type="button" className="sm" disabled={!!saving} onClick={() => void here()}>
                <Icon name="mapPin" size={14} /> {saving === "here" ? "Определяю…" : hasPoint(stop) ? "Уточнить точку: я здесь" : "Поставить точку: я здесь"}
              </button>
              <button type="button" className="sm" onClick={onPlaceOnMap}><Icon name="globe" size={14} /> Указать на карте</button>
              <span className="spacer" />
              <button type="button" className="icon-btn" disabled={busy || index <= 0} aria-label="Раньше в порядке объезда" onClick={() => onMove(-1)}>
                <Icon name="chevronLeft" size={18} style={{ transform: "rotate(90deg)" }} />
              </button>
              <button type="button" className="icon-btn" disabled={busy || index >= total - 1} aria-label="Позже в порядке объезда" onClick={() => onMove(1)}>
                <Icon name="chevronRight" size={18} style={{ transform: "rotate(90deg)" }} />
              </button>
              {stop.verifications_total === 0 && (
                <button type="button" className="icon-btn danger" disabled={busy} aria-label="Убрать адрес" onClick={onRemove}>
                  <Icon name="trash" size={17} />
                </button>
              )}
            </div>
          </>
        )}
        {local && <Alert onClose={() => { setLocal(""); onError(""); }}>{local}</Alert>}
      </div>
    </Modal>
  );
}

function AddStopModal({
  trip, procurement, onClose, onAdded,
}: {
  trip: Trip;
  procurement: Procurement | null;
  onClose: () => void;
  onAdded: (stop: TripStop, work: boolean) => void;
}) {
  const byContract = trip.kind === "procurement";
  // «На месте» по умолчанию, когда командировка уже идёт или началась по датам.
  const underway = trip.status === "active" || (trip.date_start != null && trip.date_start <= today());
  const [onSite, setOnSite] = useState(underway);
  const [address, setAddress] = useState("");
  const [picked, setPicked] = useState<AddressSuggestion | null>(null);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [showList, setShowList] = useState(false);
  const [siteId, setSiteId] = useState<number | "">("");
  const [clientName, setClientName] = useState("");
  const [phone, setPhone] = useState("");
  const [count, setCount] = useState("");
  const [note, setNote] = useState("");
  const [restricted, setRestricted] = useState(false);
  const [accessNote, setAccessNote] = useState("");
  const [point, setPoint] = useState<Point | null>(null);
  const [locating, setLocating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = useRef(0);

  // Знакомые объекты заказчика, которых в этой командировке ещё нет.
  const knownSites = useMemo(() => {
    const taken = new Set(trip.stops.map((stop) => stop.site_id));
    return (procurement?.sites ?? []).filter((site) => !taken.has(site.id));
  }, [procurement, trip.stops]);

  const here = useCallback(async (silent = false) => {
    setLocating(true);
    try {
      setPoint(await locateMe(silent ? 8000 : 12_000));
      if (!silent) setError("");
    } catch (exc) {
      if (!silent) setError(errorText(exc, "Не удалось определить местоположение"));
    } finally {
      setLocating(false);
    }
  }, []);

  // На месте берём точку сразу — но только если разрешение уже дано: спрашивать его без нажатия невежливо.
  useEffect(() => {
    if (!onSite || point) return;
    const permissions = navigator.permissions;
    if (!permissions?.query) return;
    permissions
      .query({ name: "geolocation" })
      .then((result) => {
        if (result.state === "granted") void here(true);
      })
      .catch(() => undefined);
  }, [onSite, point, here]);

  useEffect(() => {
    const query = address.trim();
    if (query.length < 4 || picked?.value === address || siteId) {
      setSuggestions([]);
      return;
    }
    const current = ++run.current;
    const timer = window.setTimeout(() => {
      void suggestAddress(trip.destination && !query.toLowerCase().includes(trip.destination.toLowerCase().split(" ")[0]) ? `${trip.destination} ${query}` : query)
        .then((response) => {
          if (current !== run.current) return;
          setSuggestions(response.results);
          setShowList(response.results.length > 0);
        })
        .catch(() => undefined);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [address, picked, siteId, trip.destination]);

  const pick = (item: AddressSuggestion) => {
    setPicked(item);
    setAddress(item.value);
    setShowList(false);
  };

  const submit = async (work: boolean) => {
    if (!siteId && !address.trim()) {
      setError(byContract ? "Укажите объект" : "Укажите адрес");
      return;
    }
    setBusy(true);
    setError("");
    // Точка: с телефона (я стою у двери) точнее подсказки; подсказка — когда адрес в плане.
    const fromSuggestion =
      picked && picked.value === address && picked.latitude != null && picked.longitude != null
        ? { latitude: picked.latitude, longitude: picked.longitude }
        : null;
    const coords = (onSite ? point : null) ?? fromSuggestion ?? point;
    try {
      const stop = await addTripStop(trip.id, {
        ...(siteId ? { site_id: Number(siteId) } : { address: address.trim() }),
        client_name: byContract ? "" : clientName.trim(),
        contact_name: clientName.trim(),
        contact_phone: phone.trim(),
        planned_count: count ? Number(count) : null,
        note: note.trim(),
        added_on_site: onSite,
        ...(coords ?? {}),
        ...(picked && picked.value === address
          ? { postal_code: picked.postal_code, fias_id: picked.fias_id, district: picked.district }
          : {}),
        ...(byContract && !siteId ? { is_restricted: restricted, access_note: accessNote.trim() } : {}),
      });
      onAdded(stop, work);
    } catch (exc) {
      setError(errorText(exc, "Не удалось добавить адрес"));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={byContract ? "Объект заказчика" : "Новый адрес"}
      subtitle={trip.title}
      footer={
        <>
          <button type="button" disabled={busy} onClick={() => void submit(false)}>{onSite ? "Только добавить" : "Добавить в план"}</button>
          {onSite && (
            <button type="button" className="primary" disabled={busy} onClick={() => void submit(true)}>
              {busy ? "Добавляю…" : "Добавить и поверять"}
            </button>
          )}
        </>
      }
    >
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(onSite);
        }}
      >
        <Segmented
          value={onSite ? "site" : "plan"}
          options={[
            { value: "site", label: "Я на месте", icon: "mapPin" },
            { value: "plan", label: "В план", icon: "calendar" },
          ]}
          onChange={(value) => setOnSite(value === "site")}
        />

        {byContract && knownSites.length > 0 && (
          <div className="field">
            <label htmlFor="stop-site">Объект</label>
            <select id="stop-site" value={siteId} onChange={(event) => setSiteId(event.target.value ? Number(event.target.value) : "")}>
              <option value="">Новый объект…</option>
              {knownSites.map((site) => (
                <option key={site.id} value={site.id}>{site.address}{site.is_restricted ? " (режимный)" : ""}</option>
              ))}
            </select>
          </div>
        )}

        {!siteId && (
          <div className="field suggest-field">
            <label htmlFor="stop-address">{byContract ? "Название и адрес объекта" : "Адрес"}</label>
            <input
              id="stop-address"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              onFocus={() => setShowList(suggestions.length > 0)}
              onBlur={() => window.setTimeout(() => setShowList(false), 150)}
              placeholder={byContract ? "Котельная № 4, ул. Заводская, 12" : "Улица, дом, квартира"}
              autoComplete="off"
              autoFocus
            />
            {showList && (
              <ul className="suggestions">
                {suggestions.map((item) => (
                  <li key={item.value} onMouseDown={() => pick(item)}>
                    <span>{item.value}</span>
                    {item.postal_code && <span className="sub">{item.postal_code}</span>}
                  </li>
                ))}
              </ul>
            )}
            <div className="trip-here">
              <button type="button" className="sm" disabled={locating} onClick={() => void here()}>
                <Icon name="mapPin" size={14} /> {locating ? "Определяю…" : point ? "Обновить точку" : "Я здесь"}
              </button>
              <span className={`hint ${point ? "ok" : ""}`}>
                {point
                  ? "Точка с телефона — адрес встанет на карту там, где вы стоите"
                  : onSite
                    ? "Нажмите у двери — адрес сразу окажется на карте"
                    : "Без точки адрес найдём по тексту"}
              </span>
            </div>
          </div>
        )}

        <div className="field-row">
          <div className="field">
            <label htmlFor="stop-client">{byContract ? "Кто встречает" : "ФИО"} <span className="opt">— необязательно</span></label>
            <input id="stop-client" value={clientName} onChange={(event) => setClientName(event.target.value)} autoComplete="off" />
          </div>
          <div className="field">
            <label htmlFor="stop-new-phone">Телефон</label>
            <input id="stop-new-phone" type="tel" inputMode="tel" value={phone} onChange={(event) => setPhone(event.target.value)} autoComplete="off" />
          </div>
        </div>
        <div className="field-row">
          <div className="field" style={{ flex: "0 0 130px" }}>
            <label htmlFor="stop-new-count">Приборов</label>
            <input id="stop-new-count" inputMode="numeric" value={count} onChange={(event) => setCount(event.target.value.replace(/\D/g, ""))} placeholder="сколько ждём" />
          </div>
          <div className="field">
            <label htmlFor="stop-new-note">Заметка</label>
            <input id="stop-new-note" value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} />
          </div>
        </div>

        {byContract && !siteId && (
          <>
            <Switch checked={restricted} onChange={setRestricted} label="Режимный объект" hint="Телефон пронести нельзя — поверка по бумажному бланку" />
            <div className="field">
              <label htmlFor="stop-access">Порядок допуска <span className="opt">— необязательно</span></label>
              <input id="stop-access" value={accessNote} onChange={(event) => setAccessNote(event.target.value)} placeholder="Проходная № 2, паспорт, заявка за сутки" />
            </div>
          </>
        )}
        {!byContract && !clientName.trim() && (
          <div className="hint">Без ФИО владельцем в журнале будет «Физическое лицо».</div>
        )}
        {error && <Alert>{error}</Alert>}
      </form>
    </Modal>
  );
}

function BulkModal({ trip, onClose, onAdded }: { trip: Trip; onClose: () => void; onAdded: (trip: Trip, notice: string) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lines = text.split("\n").filter((line) => line.trim()).length;

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await addTripStopsBulk(trip.id, text);
      const skipped = result.skipped.length ? ` Пропущено ${result.skipped.length}: ${result.skipped[0]}` : "";
      onAdded(result.trip, `Добавлено: ${addresses(result.added)}.${skipped}`);
    } catch (exc) {
      setError(errorText(exc, "Не удалось добавить адреса"));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Адреса списком"
      subtitle={trip.title}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy || lines === 0} onClick={() => void submit()}>
            {busy ? "Добавляю…" : lines ? `Добавить ${addresses(lines)}` : "Добавить"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <div className="field">
          <label htmlFor="bulk-text">По одному адресу в строке</label>
          <textarea
            id="bulk-text"
            rows={9}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={"Слободской, ул Советская, 10, кв 5; Иванова М. П.; +7 900 000-00-01; 2\nСлободской, ул Ленина, 3\nд Стулово, ул Мира, 14; Петров"}
            autoFocus
          />
          <div className="hint">
            После адреса через точку с запятой можно дописать ФИО, телефон и сколько приборов ждём — в любом порядке.
            {trip.kind === "procurement" && " У командировки по закупке клиент — всегда заказчик, ФИО уйдёт в «кто встречает»."}
          </div>
        </div>
        {error && <Alert>{error}</Alert>}
      </div>
    </Modal>
  );
}

function EditTripModal({
  trip, isManager, onClose, onSaved, onDelete,
}: {
  trip: Trip;
  isManager: boolean;
  onClose: () => void;
  onSaved: (trip: Trip) => void;
  onDelete: () => void;
}) {
  const [title, setTitle] = useState(trip.title);
  const [destination, setDestination] = useState(trip.destination);
  const [dateStart, setDateStart] = useState(trip.date_start ?? "");
  const [dateEnd, setDateEnd] = useState(trip.date_end ?? "");
  const [note, setNote] = useState(trip.note);
  const [members, setMembers] = useState<number[]>(trip.members.map((member) => member.id));
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchEmployees().then(setEmployees).catch(() => undefined);
  }, []);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      onSaved(
        await updateTrip(trip.id, {
          title: title.trim(), destination: destination.trim(), date_start: dateStart || null, date_end: dateEnd || null,
          note, member_ids: members,
        }),
      );
    } catch (exc) {
      setError(errorText(exc, "Не удалось сохранить"));
      setBusy(false);
    }
  };

  const toggle = (id: number) =>
    setMembers((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));

  return (
    <Modal
      open
      onClose={onClose}
      title="Командировка"
      footer={
        <>
          <button type="button" className="ghost danger" onClick={onDelete}><Icon name="trash" size={15} /> Удалить</button>
          <span className="spacer" style={{ flex: 1 }} />
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        </>
      }
    >
      <div className="form-stack">
        <div className="field">
          <label htmlFor="edit-title">Название</label>
          <input id="edit-title" value={title} onChange={(event) => setTitle(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="edit-destination">Куда</label>
          <input id="edit-destination" value={destination} onChange={(event) => setDestination(event.target.value)} />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="edit-start">Выезд</label>
            <input id="edit-start" type="date" value={dateStart} onChange={(event) => setDateStart(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="edit-end">Возвращение</label>
            <input id="edit-end" type="date" value={dateEnd} min={dateStart} onChange={(event) => setDateEnd(event.target.value)} />
          </div>
        </div>
        {(isManager || trip.can_edit) && employees.length > 1 && (
          <div className="field">
            <span className="field-label">Кто едет, кроме старшего ({trip.lead.full_name})</span>
            <div className="chip-row">
              {employees.filter((employee) => employee.id !== trip.lead.id).map((employee) => (
                <button
                  type="button"
                  key={employee.id}
                  className={`chip ${members.includes(employee.id) ? "active" : ""}`}
                  aria-pressed={members.includes(employee.id)}
                  onClick={() => toggle(employee.id)}
                >
                  {members.includes(employee.id) && <Icon name="check" size={13} />}
                  {employee.full_name}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="field">
          <label htmlFor="edit-note">Примечание</label>
          <textarea id="edit-note" rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Где ночуем, что взять, с кем договорились" />
        </div>
        {error && <Alert>{error}</Alert>}
      </div>
    </Modal>
  );
}

function NextVisitModal({ trip, onClose, onCreated }: { trip: Trip; onClose: () => void; onCreated: (id: number) => void }) {
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const carried = trip.summary.stops_waiting + trip.summary.stops_no_answer + trip.summary.stops_postponed;

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await startNextVisit(trip.id, { date_start: dateStart || null, date_end: dateEnd || null });
      onCreated(next.id);
    } catch (exc) {
      setError(errorText(exc, "Не удалось завести следующий заход"));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Следующий заход"
      subtitle={trip.procurement ? `${trip.procurement.customer} · ${trip.procurement.label}` : trip.title}
      size="sm"
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "Создаю…" : "Создать"}</button>
        </>
      }
    >
      <div className="form-stack">
        <p className="hint" style={{ margin: 0 }}>
          Заведу новую командировку по той же закупке с тем же составом и перенесу в неё {addresses(carried)}:
          те, что ждут, где не открыли и где договорились о переносе. Отказы и выполненные остаются здесь.
        </p>
        <div className="field-row">
          <div className="field">
            <label htmlFor="next-start">Выезд <span className="opt">— если известно</span></label>
            <input id="next-start" type="date" value={dateStart} onChange={(event) => setDateStart(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="next-end">Возвращение</label>
            <input id="next-end" type="date" value={dateEnd} min={dateStart} onChange={(event) => setDateEnd(event.target.value)} />
          </div>
        </div>
        {dateStart && <div className="hint">Выезд {ruDate(dateStart)}</div>}
        {error && <Alert>{error}</Alert>}
      </div>
    </Modal>
  );
}
