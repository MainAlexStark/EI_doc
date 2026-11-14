import { useCallback, useEffect, useState } from "react";
import {
  createTrip,
  fetchEmployees,
  fetchProcurements,
  fetchTrips,
  type Employee,
  type Me,
  type ProcurementRow,
  type TripBrief,
} from "../api";
import Icon from "../ui/Icon";
import { Modal } from "../ui/Modal";
import { Alert, EmptyState, PageActions, Segmented, Spinner } from "../ui/controls";
import ProcurementView, { ProcurementForm } from "./Procurements";
import TripDetail from "./TripDetail";
import { DeadlinePill } from "./TripBits";
import { TRIP_TONE, dateRange, errorText, instruments, isoDay, ruDate } from "./tripUtil";

/** Раздел «Выезды» (claude/trips.md).
 *
 * Всё, где есть адреса и карта, в одном списке: день поверителя в городе
 * (заводится сам, когда подтверждают заявку с сайта), командировка (район,
 * даты) и командировка по закупке, где главное — контракт: номер, документы,
 * пропуска, объём. У закупки заходов сколько угодно, поэтому у неё своя
 * карточка, а выезд — один заход.
 *
 * Где человек был — командировка или закупка — помнит sessionStorage: телефон
 * часто выгружает вкладку, пока ходишь по квартирам, и возвращаться каждый раз
 * в общий список было бы мучением. */

type View = { kind: "list" } | { kind: "trip"; id: number } | { kind: "procurement"; id: number };
type Section = "trips" | "procurements";
type Period = "open" | "closed";

const VIEW_KEY = "ei_doc_trips_view";

const loadView = (): View => {
  try {
    const saved = JSON.parse(sessionStorage.getItem(VIEW_KEY) ?? "null") as View | null;
    if (saved && (saved.kind === "trip" || saved.kind === "procurement") && Number.isInteger(saved.id)) return saved;
  } catch {
    /* хранилище недоступно — начинаем со списка */
  }
  return { kind: "list" };
};

const saveView = (view: View) => {
  try {
    sessionStorage.setItem(VIEW_KEY, JSON.stringify(view));
  } catch {
    /* не страшно */
  }
};

export default function Trips({ me, isManager }: { me: Me | null; isManager: boolean }) {
  const [view, setViewState] = useState<View>(loadView);
  const [section, setSection] = useState<Section>("trips");
  const [period, setPeriod] = useState<Period>("open");
  const [trips, setTrips] = useState<TripBrief[] | null>(null);
  const [procurements, setProcurements] = useState<ProcurementRow[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState<null | "trip" | "procurement">(null);
  const [forProcurement, setForProcurement] = useState<number | null>(null);

  const setView = useCallback((next: View) => {
    saveView(next);
    setViewState(next);
    window.scrollTo({ top: 0 });
  }, []);

  const reload = useCallback(() => {
    setError("");
    if (section === "trips") {
      fetchTrips({ state: period })
        .then(setTrips)
        .catch((exc) => setError(errorText(exc, "Не удалось загрузить командировки")));
    } else {
      fetchProcurements({ state: period })
        .then(setProcurements)
        .catch((exc) => setError(errorText(exc, "Не удалось загрузить закупки")));
    }
  }, [section, period]);

  useEffect(() => {
    if (view.kind === "list") reload();
  }, [view.kind, reload]);

  if (!me) return <Spinner />;

  const modals = (
    <>
      {creating === "trip" && (
        <NewTripModal
          me={me}
          isManager={isManager}
          procurementId={forProcurement}
          onClose={() => setCreating(null)}
          onCreated={(id) => {
            setCreating(null);
            setView({ kind: "trip", id });
          }}
          onNewProcurement={() => setCreating("procurement")}
        />
      )}
      {creating === "procurement" && (
        <ProcurementForm
          onClose={() => setCreating(null)}
          onSaved={(item) => {
            setCreating(null);
            setView({ kind: "procurement", id: item.id });
          }}
        />
      )}
    </>
  );

  if (view.kind === "trip") {
    return (
      <TripDetail
        key={view.id}
        tripId={view.id}
        me={me}
        isManager={isManager}
        onBack={() => setView({ kind: "list" })}
        onOpenProcurement={(id) => setView({ kind: "procurement", id })}
        onOpenTrip={(id) => setView({ kind: "trip", id })}
      />
    );
  }

  if (view.kind === "procurement") {
    return (
      <>
      <ProcurementView
        key={view.id}
        procurementId={view.id}
        onBack={() => {
          setSection("procurements");
          setView({ kind: "list" });
        }}
        onOpenTrip={(id) => setView({ kind: "trip", id })}
        onNewVisit={(id) => {
          setForProcurement(id);
          setCreating("trip");
        }}
      />
      {modals}
      </>
    );
  }

  const list = section === "trips" ? trips : procurements;

  return (
    <div className="narrow">
      <PageActions>
        <button
          type="button"
          className="primary"
          onClick={() => {
            setForProcurement(null);
            setCreating(section === "procurements" && isManager ? "procurement" : "trip");
          }}
        >
          <Icon name="plus" size={16} /> {section === "procurements" && isManager ? "Закупка" : "Выезд"}
        </button>
      </PageActions>

      <div className="toolbar">
        <Segmented
          value={section}
          options={[
            { value: "trips", label: "Выезды", icon: "car" },
            { value: "procurements", label: "Закупки", icon: "briefcase" },
          ]}
          onChange={setSection}
        />
        <span className="spacer" />
        <Segmented
          size="sm"
          value={period}
          options={[
            { value: "open", label: "Идут" },
            { value: "closed", label: "Архив" },
          ]}
          onChange={setPeriod}
        />
      </div>

      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {list == null && !error && <Spinner />}

      {section === "trips" && trips != null && trips.length === 0 && (
        <EmptyState
          icon="car"
          title={period === "open" ? "Выездов пока нет" : "В архиве пусто"}
          action={
            period === "open" ? (
              <button type="button" className="primary" onClick={() => setCreating("trip")}>
                <Icon name="plus" size={16} /> Завести выезд
              </button>
            ) : undefined
          }
        >
          {period === "open" &&
            "Подтверждённые заявки с сайта сами встают в день поверителя. Командировку заведите кнопкой «Выезд»: адреса из плана, остальные — на месте."}
        </EmptyState>
      )}

      {section === "trips" && trips != null && trips.length > 0 && (
        <div className="card-list">
          {trips.map((trip) => (
            <TripCard key={trip.id} trip={trip} showLead={isManager} onOpen={() => setView({ kind: "trip", id: trip.id })} />
          ))}
        </div>
      )}

      {section === "procurements" && procurements != null && procurements.length === 0 && (
        <EmptyState icon="briefcase" title={period === "open" ? "Закупок в работе нет" : "В архиве пусто"}>
          {period === "open" &&
            (isManager
              ? "Заведите закупку — номер, заказчик, контракт. Командировки по ней станут её заходами."
              : "Закупки заводит руководитель. Здесь появятся те, по которым вы едете.")}
        </EmptyState>
      )}

      {section === "procurements" && procurements != null && procurements.length > 0 && (
        <div className="card-list">
          {procurements.map((item) => (
            <ProcurementCard key={item.id} item={item} onOpen={() => setView({ kind: "procurement", id: item.id })} />
          ))}
        </div>
      )}

      {modals}
    </div>
  );
}

function TripCard({ trip, showLead, onOpen }: { trip: TripBrief; showLead: boolean; onOpen: () => void }) {
  const people = [trip.lead.full_name, ...trip.members.map((member) => member.full_name)];
  const line = [
    dateRange(trip.date_start, trip.date_end),
    trip.destination && trip.destination !== trip.title ? trip.destination : "",
    showLead ? (people.length > 1 ? `${people[0]} +${people.length - 1}` : people[0]) : "",
  ].filter(Boolean);
  return (
    <button type="button" className="card tap" onClick={onOpen}>
      <div className="order-row">
        <div className="grow">
          <b>{trip.title}</b>
          <div className="sub">{line.join(" · ")}</div>
          <div className="trip-tags">
            <span className={`pill ${TRIP_TONE[trip.status]}`}>{trip.status_display}</span>
            {trip.kind === "city" && <span className="pill info plain">город</span>}
            {trip.kind === "classic" && <span className="pill muted plain">командировка</span>}
            {trip.procurement && <span className="pill purple plain">Закупка {trip.procurement.label}</span>}
          </div>
        </div>
        <div className="order-progress">
          {trip.stops_total > 0 ? `${trip.stops_done} из ${trip.stops_total}` : "нет адресов"}
          {trip.stops_total > 0 && (
            <div className="mini-bar"><i style={{ width: `${(trip.stops_done / trip.stops_total) * 100}%` }} /></div>
          )}
          {trip.instruments_entered > 0 && <small>{instruments(trip.instruments_entered)}</small>}
        </div>
      </div>
    </button>
  );
}

function ProcurementCard({ item, onOpen }: { item: ProcurementRow; onOpen: () => void }) {
  const { planned, ready } = item.progress;
  const closed = item.status === "done" || item.status === "cancelled";
  return (
    <button type="button" className="card tap" onClick={onOpen}>
      <div className="order-row">
        <div className="grow">
          <b>{item.customer}</b>
          <div className="sub">{[item.label, item.law_display, item.title].filter(Boolean).join(" · ")}</div>
          <div className="trip-tags">
            <span className={`pill ${item.status === "active" ? "ok" : item.status === "cancelled" ? "bad" : "muted"}`}>
              {item.status_display}
            </span>
            <DeadlinePill deadline={item.deadline} closed={closed} />
            {item.trips_total > 0 && <span className="pill muted plain">заходов: {item.trips_total}</span>}
          </div>
        </div>
        <div className="order-progress">
          {planned > 0 ? `${ready} из ${planned}` : ready > 0 ? instruments(ready) : "—"}
          {planned > 0 && <div className="mini-bar"><i style={{ width: `${Math.min(100, (ready / planned) * 100)}%` }} /></div>}
        </div>
      </div>
    </button>
  );
}

function NewTripModal({
  me, isManager, procurementId, onClose, onCreated, onNewProcurement,
}: {
  me: Me;
  isManager: boolean;
  procurementId: number | null;
  onClose: () => void;
  onCreated: (id: number) => void;
  onNewProcurement: () => void;
}) {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const [kind, setKind] = useState<"city" | "classic" | "procurement">(procurementId ? "procurement" : "city");
  const [destination, setDestination] = useState("");
  const [title, setTitle] = useState("");
  const [dateStart, setDateStart] = useState(isoDay(tomorrow));
  const [dateEnd, setDateEnd] = useState("");
  const [procurement, setProcurement] = useState<number | "">(procurementId ?? "");
  const [leadId, setLeadId] = useState<number | "">(me.employee?.id ?? "");
  const [members, setMembers] = useState<number[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [procurements, setProcurements] = useState<ProcurementRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchEmployees().then(setEmployees).catch(() => undefined);
    fetchProcurements({ state: "open" }).then(setProcurements).catch(() => undefined);
  }, []);

  const save = async () => {
    if (kind === "classic" && !destination.trim() && !title.trim()) {
      setError("Напишите, куда едете");
      return;
    }
    if (kind === "procurement" && !procurement) {
      setError("Выберите закупку");
      return;
    }
    if (!leadId) {
      setError("Выберите старшего");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const city = kind === "city";
      const trip = await createTrip({
        kind,
        title: title.trim() || (city ? `Киров — ${ruDate(dateStart)}` : ""),
        destination: city ? destination.trim() || "Киров" : destination.trim(),
        date_start: dateStart || null,
        date_end: city ? dateStart || null : dateEnd || null,
        lead_id: Number(leadId),
        member_ids: members,
        procurement_id: kind === "procurement" ? Number(procurement) : null,
      });
      onCreated(trip.id);
    } catch (exc) {
      setError(errorText(exc, "Не удалось завести командировку"));
      setBusy(false);
    }
  };

  const toggle = (id: number) =>
    setMembers((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  const others = employees.filter((employee) => employee.id !== leadId);

  return (
    <Modal
      open
      onClose={onClose}
      title="Новый выезд"
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Создаю…" : "Создать"}
          </button>
        </>
      }
    >
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Segmented
          value={kind}
          options={[
            { value: "city", label: "Город", icon: "mapPin" },
            { value: "classic", label: "Командировка", icon: "car" },
            { value: "procurement", label: "По закупке", icon: "briefcase" },
          ]}
          onChange={setKind}
        />
        {kind === "city" && (
          <div className="hint">
            День по городу. Обычно его заводить не нужно: подтверждённые заявки с сайта сами встают в день исполнителя.
          </div>
        )}

        {kind === "procurement" && (
          <div className="field">
            <label htmlFor="trip-procurement">Закупка</label>
            <select
              id="trip-procurement"
              value={procurement}
              onChange={(event) => setProcurement(event.target.value ? Number(event.target.value) : "")}
            >
              <option value="">— выберите —</option>
              {procurements.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.customer} · {item.label}
                </option>
              ))}
            </select>
            <div className="hint">
              Командировка станет очередным заходом по этому контракту.{" "}
              {isManager && (
                <button type="button" className="link" onClick={onNewProcurement}>
                  Завести новую закупку
                </button>
              )}
              {!isManager && procurements.length === 0 && "Закупки заводит руководитель."}
            </div>
          </div>
        )}

        {kind !== "city" && <div className="field">
          <label htmlFor="trip-destination">Куда</label>
          <input
            id="trip-destination"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            placeholder="Район или населённый пункт"
            autoFocus={kind === "classic"}
          />
        </div>}
        <div className="field">
          <label htmlFor="trip-title">Название <span className="opt">— необязательно</span></label>
          <input
            id="trip-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder={kind === "procurement" ? "Заказчик — заход N" : destination || "Как называть в списке"}
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="trip-start">{kind === "city" ? "День" : "Выезд"}</label>
            <input id="trip-start" type="date" value={dateStart} onChange={(event) => setDateStart(event.target.value)} />
          </div>
          {kind !== "city" && <div className="field">
            <label htmlFor="trip-end">Возвращение <span className="opt">— если известно</span></label>
            <input id="trip-end" type="date" value={dateEnd} min={dateStart} onChange={(event) => setDateEnd(event.target.value)} />
          </div>}
        </div>

        {isManager && (
          <div className="field">
            <label htmlFor="trip-lead">Старший</label>
            <select
              id="trip-lead"
              value={leadId}
              onChange={(event) => {
                const next = event.target.value ? Number(event.target.value) : "";
                setLeadId(next);
                setMembers((current) => current.filter((id) => id !== next));
              }}
            >
              <option value="">— выберите —</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>{employee.full_name}</option>
              ))}
            </select>
          </div>
        )}

        {others.length > 0 && (
          <div className="field">
            <span className="field-label">Кто едет ещё</span>
            <div className="chip-row">
              {others.map((employee) => (
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
        {error && <Alert>{error}</Alert>}
      </form>
    </Modal>
  );
}
