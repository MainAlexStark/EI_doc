import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  can,
  createTrip,
  downloadFile,
  fetchDemand,
  fetchEmployees,
  fetchMapConfig,
  fetchStandardOptions,
  fetchTrip,
  fetchTrips,
  optimizeTrip,
  removeStop,
  reserveStandards,
  unreserveStandard,
  updateStop,
  updateTrip,
  type DemandGroup,
  type Employee,
  type MapConfig,
  type Me,
  type Trip,
} from "../api";
import type { MapLine, MapMarker } from "./TripMap";

const TripMap = lazy(() => import("./TripMap"));

const KIND_COLOR = { work_order: "#0a84ff", request: "#ff9f0a", expiring: "#8e8e93" } as const;
const KIND_LABEL = { work_order: "наряд", request: "заявка", expiring: "истекает МПИ" } as const;
const DAY_COLORS = ["#0a84ff", "#30d158", "#ff9f0a", "#bf5af2", "#ff375f", "#64d2ff"];

const dateRu = (value: string | null | undefined) => (value ? new Date(value).toLocaleDateString("ru-RU") : "—");
const time = (value: string | null) => (value ? value.slice(0, 5) : "");
const isoToday = (offset = 0) => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toISOString().slice(0, 10);
};

/**
 * «Выезды»: спрос по районам на карте → командировка из нарядов → маршрут по
 * дням с ETA → брони эталонов → PDF-пакет на выезд → план/факт.
 * Поверитель видит свои командировки и отмечает остановки.
 */
export default function Trips({ me }: { me: Me | null }) {
  const planner = can(me, "logistics.plan_trips");
  const [config, setConfig] = useState<MapConfig | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [open, setOpen] = useState<Trip | null>(null);
  const [demand, setDemand] = useState<DemandGroup[]>([]);
  const [days, setDays] = useState(60);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const [cfg, list] = await Promise.all([fetchMapConfig(), fetchTrips()]);
      setConfig(cfg);
      setTrips(list);
      if (planner) setDemand((await fetchDemand(days)).groups);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить командировки");
    }
  }, [planner, days]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не получилось");
    } finally {
      setBusy(false);
    }
  };

  const openTrip = (id: number) => run(async () => setOpen(await fetchTrip(id)));

  if (open) {
    return (
      <TripDetail
        trip={open}
        config={config}
        planner={planner}
        busy={busy}
        error={error}
        message={message}
        onBack={() => {
          setOpen(null);
          void load();
        }}
        run={run}
        setTrip={setOpen}
        setMessage={setMessage}
      />
    );
  }

  return (
    <div className="trips">
      {error && <div className="error">{error}</div>}
      {planner && (
        <>
          <div className="toolbar">
            <label>
              Спрос на{" "}
              <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                {[14, 30, 60, 90, 180].map((d) => (
                  <option key={d} value={d}>{d} дней</option>
                ))}
              </select>
            </label>
            <span className="legend">
              {(Object.keys(KIND_COLOR) as (keyof typeof KIND_COLOR)[]).map((kind) => (
                <span key={kind}><i style={{ background: KIND_COLOR[kind] }} /> {KIND_LABEL[kind]}</span>
              ))}
            </span>
          </div>
          {config && <DemandMap config={config} groups={demand} />}
          <div className="scopes">
            {demand.map((group) => (
              <DemandCard key={group.district_id ?? "none"} group={group} busy={busy} onPlan={(input) =>
                run(async () => {
                  const trip = await createTrip(input);
                  setOpen(trip);
                })
              } />
            ))}
            {demand.length === 0 && <div className="empty">Спроса в этом окне нет</div>}
          </div>
          <h2 className="section-title">Командировки</h2>
        </>
      )}
      <div className="scopes">
        {trips.map((trip) => (
          <button type="button" className="scope trip-card" key={trip.id} onClick={() => void openTrip(trip.id)}>
            <h3>{trip.title}</h3>
            <div className="who">
              {dateRu(trip.date_from)} — {dateRu(trip.date_to)} · {trip.status_display}
            </div>
            <div className="counts">
              <span className="pill muted">остановок: {trip.stops_count}</span>
              {trip.employees.map((e) => <span className="pill muted" key={e.id}>{e.name}</span>)}
            </div>
          </button>
        ))}
        {trips.length === 0 && <div className="empty">Командировок пока нет</div>}
      </div>
    </div>
  );
}

function DemandMap({ config, groups }: { config: MapConfig; groups: DemandGroup[] }) {
  const markers = useMemo<MapMarker[]>(
    () =>
      groups.flatMap((g) =>
        g.points
          .filter((p) => p.lat !== null && p.lon !== null)
          .map((p) => ({ lat: p.lat as number, lon: p.lon as number, label: p.label, color: KIND_COLOR[p.kind] })),
      ),
    [groups],
  );
  return (
    <Suspense fallback={<div className="loading">Карта загружается…</div>}>
      <TripMap config={config} markers={markers} height={360} />
    </Suspense>
  );
}

function DemandCard({ group, busy, onPlan }: {
  group: DemandGroup;
  busy: boolean;
  onPlan: (input: Parameters<typeof createTrip>[0]) => void;
}) {
  const [from, setFrom] = useState(isoToday(7));
  const [to, setTo] = useState(isoToday(8));
  const workOrders = group.points.filter((p) => p.kind === "work_order" && p.id !== null).map((p) => p.id as number);
  return (
    <div className="scope">
      <h3>{group.district}</h3>
      <div className="who">
        {group.earliest ? `ближайшая дата ${dateRu(group.earliest)}` : "без дат"}
      </div>
      <div className="counts">
        <span className="pill">нарядов: {group.work_orders}</span>
        <span className="pill muted">заявок: {group.requests}</span>
        <span className="pill muted">истекает МПИ: {group.expiring}</span>
      </div>
      {workOrders.length > 0 && (
        <div className="actions">
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="с" />
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="по" />
          <button className="primary" disabled={busy} onClick={() =>
            onPlan({ title: group.district, date_from: from, date_to: to < from ? from : to,
                     district_id: group.district_id, work_order_ids: workOrders })}>
            Командировка из {workOrders.length} нарядов
          </button>
        </div>
      )}
      {group.requests + group.expiring > 0 && (
        <div className="hint">Заявки сначала подтверждаются во вкладке «Заявки» (станут нарядами); по истекающим МПИ — обзвон.</div>
      )}
    </div>
  );
}

function TripDetail({ trip, config, planner, busy, error, message, onBack, run, setTrip, setMessage }: {
  trip: Trip;
  config: MapConfig | null;
  planner: boolean;
  busy: boolean;
  error: string;
  message: string;
  onBack: () => void;
  run: (work: () => Promise<void>) => Promise<void>;
  setTrip: (trip: Trip) => void;
  setMessage: (text: string) => void;
}) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [standards, setStandards] = useState<Awaited<ReturnType<typeof fetchStandardOptions>>>([]);
  const [cost, setCost] = useState(trip.actual_cost ?? "");

  useEffect(() => {
    if (!planner) return;
    void fetchEmployees().then(setEmployees).catch(() => setEmployees([]));
    void fetchStandardOptions(trip.date_from, trip.date_to).then(setStandards).catch(() => setStandards([]));
  }, [planner, trip.date_from, trip.date_to, trip.reservations?.length]);

  const stops = trip.stops ?? [];
  const byDay = useMemo(() => {
    const groups = new Map<string, typeof stops>();
    for (const stop of stops) {
      const key = stop.day ?? "";
      groups.set(key, [...(groups.get(key) ?? []), stop]);
    }
    return [...groups.entries()].sort(([a], [b]) => (a || "9").localeCompare(b || "9"));
  }, [stops]);

  const { markers, lines } = useMemo(() => {
    const m: MapMarker[] = [];
    const l: MapLine[] = [];
    byDay.forEach(([, dayStops], index) => {
      const color = DAY_COLORS[index % DAY_COLORS.length];
      const located = dayStops.filter((s) => s.lat !== null && s.lon !== null).sort((a, b) => a.position - b.position);
      located.forEach((s) =>
        m.push({ lat: s.lat as number, lon: s.lon as number, color: s.status === "done" ? "#8e8e93" : color,
                 text: String(s.position), label: `${s.position}. ${s.address}${s.eta ? ` · ${time(s.eta)}` : ""}` }),
      );
      const coords: [number, number][] = located.map((s) => [s.lon as number, s.lat as number]);
      if (trip.depot) coords.unshift([trip.depot.lon, trip.depot.lat]);
      if (coords.length > 1) l.push({ coords, color });
    });
    if (trip.depot) m.push({ lat: trip.depot.lat, lon: trip.depot.lon, color: "#1c1c1e", text: "★", label: "Выезд" });
    return { markers: m, lines: l };
  }, [byDay, trip.depot]);

  const reserved = new Set((trip.reservations ?? []).map((r) => r.standard));
  const pf = trip.plan_fact;

  return (
    <div className="trips">
      <div className="toolbar">
        <button onClick={onBack}>← Все командировки</button>
        <span className="who">
          {dateRu(trip.date_from)} — {dateRu(trip.date_to)} · {trip.status_display}
        </span>
      </div>
      {error && <div className="error">{error}</div>}
      {message && <div className="notice"><span>{message}</span></div>}

      {pf && (
        <div className="counts">
          <span className="pill">план: {pf.stops_planned}</span>
          <span className="pill ok">выполнено: {pf.stops_done}</span>
          {pf.stops_skipped > 0 && <span className="pill">не состоялось: {pf.stops_skipped}</span>}
          <span className="pill muted">осталось: {pf.stops_left}</span>
          <span className="pill muted">поверок: {pf.verifications} (непригодно {pf.unsuitable})</span>
          {pf.cost_per_verification && <span className="pill muted">₽ на поверку: {pf.cost_per_verification}</span>}
        </div>
      )}

      <div className="actions">
        {planner && (
          <button className="primary" disabled={busy} onClick={() => void run(async () => {
            const result = await optimizeTrip(trip.id);
            setTrip(result.trip);
            const s = result.summary;
            setMessage(
              s.days.map((d) => `${dateRu(d.day)}: ${d.stops} объектов, ${Math.round(d.minutes / 6) / 10} ч` +
                (d.km !== null ? `, ${d.km} км` : "")).join("; ") +
              (s.warnings.length ? ` · ${s.warnings.join("; ")}` : "") +
              (s.days[0] ? ` (${s.days[0].source === "osrm" ? "по дорогам" : "по прямой"})` : ""),
            );
          })}>
            Построить маршрут
          </button>
        )}
        <button disabled={busy} onClick={() => void run(() => downloadFile(`/api/logistics/trips/${trip.id}/pack/`, `trip-${trip.id}.pdf`))}>
          Пакет на выезд (PDF)
        </button>
        {planner && trip.status === "planned" && (
          <button disabled={busy} onClick={() => void run(async () => setTrip(await updateTrip(trip.id, { status: "confirmed" })))}>
            Утвердить
          </button>
        )}
        {planner && trip.status === "confirmed" && (
          <button disabled={busy} onClick={() => void run(async () => setTrip(await updateTrip(trip.id, { status: "in_progress" })))}>
            Выехали
          </button>
        )}
        {planner && !["done", "cancelled"].includes(trip.status) && (
          <button disabled={busy} onClick={() => void run(async () => setTrip(await updateTrip(trip.id, { status: "cancelled" })))}>
            Отменить
          </button>
        )}
      </div>

      {config && (
        <Suspense fallback={<div className="loading">Карта загружается…</div>}>
          <TripMap config={config} markers={markers} lines={lines} />
        </Suspense>
      )}

      {byDay.map(([day, dayStops], index) => (
        <div className="scope" key={day || "none"}>
          <h3>
            <i className="day-dot" style={{ background: DAY_COLORS[index % DAY_COLORS.length] }} />{" "}
            {day ? dateRu(day) : "День не назначен"}
          </h3>
          <ol className="stops">
            {[...dayStops].sort((a, b) => a.position - b.position).map((stop) => (
              <li key={stop.id} className={`stop st-${stop.status}`}>
                <div>
                  <b>{time(stop.eta) || "—"}</b> {stop.address}
                  {stop.window_from && <span className="hint"> · окно {time(stop.window_from)}–{time(stop.window_to)}</span>}
                  {stop.restricted && <span className="pill">режимный — бланк</span>}
                  <div className="hint">
                    {stop.client}
                    {stop.work_order ? ` · наряд №${stop.work_order}` : ""} · {stop.status_display}
                    {stop.lat === null ? " · нет координат" : ""}
                  </div>
                </div>
                <div className="actions">
                  {stop.status === "planned" && (
                    <>
                      <button className="link" disabled={busy} onClick={() => void run(async () => {
                        await updateStop(stop.id, { status: "done" });
                        setTrip(await fetchTrip(trip.id));
                      })}>выполнено</button>
                      <button className="link" disabled={busy} onClick={() => void run(async () => {
                        const note = window.prompt("Почему не состоялось?", "") ?? "";
                        await updateStop(stop.id, { status: "skipped", note });
                        setTrip(await fetchTrip(trip.id));
                      })}>не состоялось</button>
                    </>
                  )}
                  {planner && (
                    <button className="link" disabled={busy} onClick={() => void run(async () => {
                      await removeStop(stop.id);
                      setTrip(await fetchTrip(trip.id));
                    })}>убрать</button>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ))}

      {planner && (
        <div className="scopes">
          <div className="scope">
            <h3>Поверители</h3>
            <div className="checklist">
              {employees.map((e) => (
                <label key={e.id}>
                  <input type="checkbox" checked={trip.employees.some((x) => x.id === e.id)} disabled={busy}
                    onChange={(ev) => void run(async () => {
                      const ids = new Set(trip.employees.map((x) => x.id));
                      if (ev.target.checked) ids.add(e.id);
                      else ids.delete(e.id);
                      setTrip(await updateTrip(trip.id, { employee_ids: [...ids] }));
                    })} />
                  {e.full_name}
                </label>
              ))}
            </div>
          </div>
          <div className="scope">
            <h3>Эталоны</h3>
            <div className="checklist">
              {standards.map((s) => {
                const otherTrip = s.busy.find((b) => b.trip !== trip.id);
                return (
                  <label key={s.id} className={otherTrip ? "muted" : ""}>
                    <input type="checkbox" checked={reserved.has(s.id)} disabled={busy || (!!otherTrip && !reserved.has(s.id))}
                      onChange={(ev) => void run(async () => {
                        if (ev.target.checked) {
                          const result = await reserveStandards(trip.id, [s.id]);
                          if (result.refused.length) setMessage(result.refused.map((r) => `${r.name}: ${r.detail}`).join("; "));
                          setTrip(result.trip);
                        } else {
                          await unreserveStandard(trip.id, s.id);
                          setTrip(await fetchTrip(trip.id));
                        }
                      })} />
                    {s.name} · {s.number} · до {dateRu(s.valid_until)}
                    {otherTrip ? ` — занят: ${otherTrip.title}` : ""}
                  </label>
                );
              })}
              {standards.length === 0 && <span className="hint">Эталонов в работе нет</span>}
            </div>
          </div>
          <div className="scope">
            <h3>Расходы</h3>
            <div className="actions">
              <input inputMode="decimal" placeholder="Факт, ₽" value={cost} onChange={(e) => setCost(e.target.value)} />
              <button disabled={busy} onClick={() => void run(async () => setTrip(await updateTrip(trip.id, { actual_cost: cost || null })))}>
                Сохранить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
