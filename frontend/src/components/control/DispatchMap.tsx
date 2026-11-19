import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  addDays, assignWork, dayLabel, fetchDispatchBoard, isoDay, teamColors,
  type AssignItem, type DispatchBoard, type MapPoint,
} from "../../dispatchApi";
import Icon from "../../ui/Icon";
import { Alert, Segmented, Spinner } from "../../ui/controls";
import AssignPicker from "./AssignPicker";

/** Карта всех выездов фирмы — для руководства.
 *
 * Каждый адрес — точка цвета своего поверителя с номером в его маршруте;
 * неназначенное (заявки с сайта и выезды без даты) — пунктирные кольца.
 * Клик по точке выбирает её, Shift/⌘+клик добавляет к выбору, Shift+протяжка
 * обводит рамкой. Выбранное отдаётся человеку одним касанием справа: адреса
 * дня в городе переезжают в его день, заявки подтверждаются нарядом. */

const TILES = "/tiles/{z}/{x}/{y}.png";
const KIROV: L.LatLngTuple = [58.6035, 49.668];
type Span = "1" | "3" | "7";

const keyOf = (p: { type: string; id: number }) => `${p.type}:${p.id}`;

function pinHtml(p: MapPoint, color: string, selected: boolean): string {
  const queue = p.type !== "stop";
  const label = queue ? (p.type === "request" ? "?" : "!") : String(p.position ?? "");
  const cls = ["disp-pin", queue ? "queue" : p.status ?? "", selected ? "selected" : ""].join(" ");
  return `<span class="${cls}" style="--c:${color}">${p.status === "done" ? "✓" : label}</span>`;
}

function MapCanvas({
  points, colors, selected, onPick, onBox, focus,
}: {
  points: MapPoint[];
  colors: Map<number, string>;
  selected: Set<string>;
  onPick: (p: MapPoint, additive: boolean) => void;
  onBox: (keys: string[]) => void;
  focus: MapPoint | null;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const fitted = useRef("");
  const latest = useRef({ points, onPick, onBox });
  latest.current = { points, onPick, onBox };

  useEffect(() => {
    if (!holder.current) return;
    const instance = L.map(holder.current, { zoomControl: true, boxZoom: false }).setView(KIROV, 11);
    instance.attributionControl.setPrefix(false);
    L.tileLayer(TILES, { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(instance);
    layer.current = L.layerGroup().addTo(instance);
    map.current = instance;

    // Shift + протяжка — рамка выбора (штатный boxZoom выключен ради неё).
    let start: L.LatLng | null = null;
    let rect: L.Rectangle | null = null;
    instance.on("mousedown", (e: L.LeafletMouseEvent) => {
      if (!e.originalEvent.shiftKey) return;
      start = e.latlng;
      instance.dragging.disable();
    });
    instance.on("mousemove", (e: L.LeafletMouseEvent) => {
      if (!start) return;
      const bounds = L.latLngBounds(start, e.latlng);
      if (rect) rect.setBounds(bounds);
      else rect = L.rectangle(bounds, { color: "#007aff", weight: 1.5, fillOpacity: 0.08, interactive: false }).addTo(instance);
    });
    const finish = () => {
      if (!start) return;
      const bounds = rect?.getBounds();
      rect?.remove();
      rect = null;
      start = null;
      instance.dragging.enable();
      if (!bounds) return;
      const keys = latest.current.points
        .filter((p) => p.latitude != null && p.longitude != null && bounds.contains([p.latitude, p.longitude]))
        .map(keyOf);
      if (keys.length) latest.current.onBox(keys);
    };
    instance.on("mouseup", finish);
    document.addEventListener("mouseup", finish);

    const observer = new ResizeObserver(() => instance.invalidateSize());
    observer.observe(holder.current);
    return () => {
      document.removeEventListener("mouseup", finish);
      observer.disconnect();
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    const group = layer.current;
    if (!instance || !group) return;
    group.clearLayers();
    // Линии маршрутов: по каждому выезду — ещё не отработанные адреса по порядку.
    const routes = new Map<number, MapPoint[]>();
    for (const p of points) {
      if (p.type === "stop" && p.trip_id && p.status === "planned") {
        routes.set(p.trip_id, [...(routes.get(p.trip_id) ?? []), p]);
      }
    }
    for (const route of routes.values()) {
      if (route.length < 2) continue;
      const color = colors.get(route[0].employee_id ?? 0) ?? "#8e8e93";
      L.polyline(
        [...route].sort((a, b) => (a.position ?? 0) - (b.position ?? 0)).map((p) => [p.latitude!, p.longitude!] as L.LatLngTuple),
        { color, weight: 3, opacity: 0.5, dashArray: "2 8", lineCap: "round", interactive: false },
      ).addTo(group);
    }
    for (const p of points) {
      const isSel = selected.has(keyOf(p));
      const color = p.type === "stop" ? colors.get(p.employee_id ?? 0) ?? "#8e8e93" : "#ff9500";
      L.marker([p.latitude!, p.longitude!], {
        icon: L.divIcon({ className: "", html: pinHtml(p, color, isSel), iconSize: [28, 28], iconAnchor: [14, 14] }),
        title: `${p.address}${p.employee ? ` — ${p.employee}` : ""}`,
        zIndexOffset: isSel ? 1000 : p.type !== "stop" ? 800 : p.status === "planned" ? 400 : 0,
      })
        .on("click", (e: L.LeafletMouseEvent) => {
          const ev = e.originalEvent;
          latest.current.onPick(p, ev.shiftKey || ev.metaKey || ev.ctrlKey);
        })
        .addTo(group);
    }
    const key = points.map(keyOf).join("|");
    if (key !== fitted.current) {
      fitted.current = key;
      instance.invalidateSize(); // карта могла только что получить размер — иначе fitBounds промахивается
      const coords = points.map((p) => [p.latitude!, p.longitude!] as L.LatLngTuple);
      if (coords.length === 1) instance.setView(coords[0], 14);
      else if (coords.length > 1) instance.fitBounds(L.latLngBounds(coords), { padding: [40, 40], maxZoom: 15 });
    }
  }, [points, colors, selected]);

  useEffect(() => {
    if (focus && focus.latitude != null && focus.longitude != null && map.current) {
      map.current.setView([focus.latitude, focus.longitude], Math.max(map.current.getZoom(), 14), { animate: true });
    }
  }, [focus]);

  return <div ref={holder} className="disp-map" />;
}

export default function DispatchMap({ onOpenTrip }: { onOpenTrip: (id: number) => void }) {
  const today = isoDay(new Date());
  const [from, setFrom] = useState(today);
  const [span, setSpan] = useState<Span>("1");
  const [board, setBoard] = useState<DispatchBoard | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [showDone, setShowDone] = useState(true);
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState<MapPoint | null>(null);
  const to = addDays(from, Number(span) - 1);

  const reload = useCallback(() => {
    setError("");
    fetchDispatchBoard(from, to)
      .then((data) => {
        setBoard(data);
        setSelected((cur) => {
          const alive = new Set([...data.points, ...data.unlocated, ...data.queue].map(keyOf));
          return new Set([...cur].filter((k) => alive.has(k)));
        });
      })
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить карту"));
  }, [from, to]);

  useEffect(() => {
    reload();
    const timer = window.setInterval(reload, 90_000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const colors = useMemo(() => teamColors((board?.employees ?? []).map((e) => e.id)), [board]);
  const all = useMemo(() => (board ? [...board.queue, ...board.points, ...board.unlocated] : []), [board]);
  const byKey = useMemo(() => new Map(all.map((p) => [keyOf(p), p])), [all]);

  const visiblePoints = useMemo(() => {
    if (!board) return [];
    const located = (p: MapPoint) => p.latitude != null && p.longitude != null;
    return [
      ...board.points.filter((p) => !hidden.has(p.employee_id ?? 0) && (showDone || p.status === "planned")),
      ...board.queue.filter(located),
    ];
  }, [board, hidden, showDone]);

  const perEmployee = useMemo(() => {
    const map = new Map<number, { total: number; done: number }>();
    for (const p of [...(board?.points ?? []), ...(board?.unlocated ?? [])]) {
      const row = map.get(p.employee_id ?? 0) ?? { total: 0, done: 0 };
      row.total += 1;
      if (p.status === "done") row.done += 1;
      map.set(p.employee_id ?? 0, row);
    }
    return map;
  }, [board]);

  if (!board) return error ? <Alert>{error}</Alert> : <Spinner label="Собираю выезды всех сотрудников…" />;

  const pick = (p: MapPoint, additive: boolean) => {
    const key = keyOf(p);
    setSelected((cur) => {
      if (additive) {
        const next = new Set(cur);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      }
      return cur.size === 1 && cur.has(key) ? new Set() : new Set([key]);
    });
  };

  const chosen = [...selected].map((k) => byKey.get(k)).filter(Boolean) as MapPoint[];
  const single = chosen.length === 1 ? chosen[0] : null;
  const loadHint = (id: number) => {
    const row = perEmployee.get(id);
    return row ? `${row.total} адр.` : "";
  };
  const people = board.employees.map((e) => ({ id: e.id, name: e.short_name, color: colors.get(e.id), hint: loadHint(e.id) }));
  const commonEmployee = chosen.length && chosen.every((p) => p.employee_id === chosen[0].employee_id) ? chosen[0].employee_id : null;
  const commonDate = chosen.length && chosen.every((p) => p.date === chosen[0].date) ? chosen[0].date : null;
  const locked = chosen.filter((p) => p.type === "stop" && p.status !== "planned");

  const assign = async (employeeId: number, date: string | null, time: string | null) => {
    const items: AssignItem[] = chosen.filter((p) => !(p.type === "stop" && p.status !== "planned")).map((p) => ({ type: p.type, id: p.id }));
    if (!items.length) return;
    setBusy(true);
    setError("");
    try {
      const result = await assignWork({ items, employee_id: employeeId, date: date ?? (span === "1" ? from : null), time });
      const who = board.employees.find((e) => e.id === employeeId)?.short_name ?? "";
      if (result.done.length) setNotice(`${who}: назначено ${result.done.length}`);
      if (result.errors.length) setError(result.errors.map((e) => e.error).join("; "));
      setSelected(new Set());
      reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось назначить");
    } finally {
      setBusy(false);
    }
  };

  const toggleHidden = (id: number) =>
    setHidden((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const unlocatedQueue = board.queue.filter((p) => p.latitude == null || p.longitude == null);

  return (
    <div className="dispatch">
      <div className="toolbar">
        <div className="date-nav">
          <button type="button" className="icon-btn" aria-label="Раньше" onClick={() => setFrom(addDays(from, -Number(span)))}>
            <Icon name="chevronLeft" size={18} />
          </button>
          <button type="button" className={from === today ? "tinted" : ""} onClick={() => setFrom(today)}>
            {span === "1" ? (from === today ? "Сегодня" : dayLabel(from)) : `${dayLabel(from, { day: "numeric", month: "short" })} – ${dayLabel(to, { day: "numeric", month: "short" })}`}
          </button>
          <button type="button" className="icon-btn" aria-label="Позже" onClick={() => setFrom(addDays(from, Number(span)))}>
            <Icon name="chevronRight" size={18} />
          </button>
        </div>
        <Segmented size="sm" value={span} onChange={setSpan}
                   options={[{ value: "1", label: "День" }, { value: "3", label: "3 дня" }, { value: "7", label: "Неделя" }]} />
        <label className="check-inline">
          <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> отработанные
        </label>
        <span className="spacer" />
        <span className="toolbar-note">Shift + клик или рамка — выбрать несколько</span>
        <button type="button" className="icon-btn" aria-label="Обновить" onClick={reload}><Icon name="refresh" size={17} /></button>
      </div>
      {notice && <Alert tone="ok" onClose={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      <div className="dispatch-split">
        <div className="disp-map-wrap">
          <MapCanvas
            points={visiblePoints}
            colors={colors}
            selected={selected}
            onPick={pick}
            onBox={(keys) => setSelected((cur) => new Set([...cur, ...keys]))}
            focus={focus}
          />
          <div className="disp-legend glass">
            {board.employees.filter((e) => perEmployee.has(e.id)).map((e) => {
              const row = perEmployee.get(e.id)!;
              return (
                <button type="button" key={e.id} className={`legend-item ${hidden.has(e.id) ? "off" : ""}`}
                        style={{ "--c": colors.get(e.id) } as CSSProperties} onClick={() => toggleHidden(e.id)}
                        title={hidden.has(e.id) ? "Показать" : "Скрыть"}>
                  <i /> {e.short_name} <small>{row.done}/{row.total}</small>
                </button>
              );
            })}
            {board.queue.length > 0 && <span className="legend-item queue"><i /> не назначено <small>{board.queue.length}</small></span>}
          </div>
        </div>

        <aside className="dispatch-side">
          {chosen.length > 0 ? (
            <div className="card white disp-selection">
              <div className="card-head">
                <div className="grow">
                  <b>{single ? single.address : `Выбрано: ${chosen.length}`}</b>
                  {single ? (
                    <div className="sub">
                      {[single.client, single.contact_phone, single.date && dayLabel(single.date),
                        single.window_start && `${single.window_start}${single.window_end ? `–${single.window_end}` : ""}`,
                        single.planned_count && `${single.planned_count} приб.`].filter(Boolean).join(" · ")}
                    </div>
                  ) : (
                    <div className="sub">{chosen.filter((p) => p.type !== "stop").length} неназначенных, {chosen.filter((p) => p.type === "stop").length} адресов</div>
                  )}
                </div>
                <button type="button" className="icon-btn" aria-label="Снять выбор" onClick={() => setSelected(new Set())}><Icon name="x" size={16} /></button>
              </div>
              {single?.type === "stop" && (
                <div className="disp-facts">
                  <span className="pill plain" style={{ background: colors.get(single.employee_id ?? 0), color: "#fff" }}>{single.employee}</span>
                  <span className={`pill ${single.status === "done" ? "ok" : single.status === "planned" ? "info" : "warn"}`}>{single.status_display}</span>
                  <button type="button" className="link" onClick={() => onOpenTrip(single.trip_id!)}>{single.trip_title} <Icon name="arrowRight" size={13} /></button>
                </div>
              )}
              {single?.type !== "stop" && single && (
                <div className="disp-facts">
                  <span className="pill warn">{single.type === "request" ? `Заявка №${single.id}` : `Наряд №${single.id} без даты`}</span>
                  {single.employee && <span className="sub">подобран: {single.employee}</span>}
                </div>
              )}
              {single?.note && <p className="hint">{single.note}</p>}
              {!single && (
                <ul className="disp-chosen">
                  {chosen.slice(0, 12).map((p) => (
                    <li key={keyOf(p)}>
                      <span className="dot" style={{ background: p.type === "stop" ? colors.get(p.employee_id ?? 0) : "#ff9500" }} />
                      <span className="grow">{p.address}</span>
                      <button type="button" className="icon-btn" aria-label="Убрать" onClick={() => pick(p, true)}><Icon name="x" size={13} /></button>
                    </li>
                  ))}
                  {chosen.length > 12 && <li className="sub">и ещё {chosen.length - 12}</li>}
                </ul>
              )}
              {locked.length > 0 && (
                <p className="hint">{locked.length === chosen.length ? "Адрес уже отмечен — не переназначается." : `Отмеченные (${locked.length}) останутся как есть.`}</p>
              )}
              {locked.length < chosen.length && (
                <>
                  <div className="field-label" style={{ marginTop: 6 }}>Отдать — одним касанием</div>
                  <AssignPicker
                    key={[...selected].join(",")}
                    people={people}
                    currentId={commonEmployee}
                    date={commonDate ?? from}
                    withTime={!!single && single.type !== "stop"}
                    busy={busy}
                    onPick={(emp, day, time) => void assign(emp, day, time)}
                  />
                </>
              )}
            </div>
          ) : (
            <p className="hint disp-tip">Нажмите на точку, чтобы переназначить адрес. Пунктирные кольца — заявки и выезды, которым ещё не дали человека.</p>
          )}

          <div className="list-section">Не назначено <span className="count">{board.queue.length}</span></div>
          {board.queue.length === 0 ? (
            <p className="hint" style={{ margin: "0 4px" }}>Всё раздано.</p>
          ) : (
            <div className="card-list">
              {board.queue.map((p) => (
                <button type="button" key={keyOf(p)} className={`card tap disp-row ${selected.has(keyOf(p)) ? "active" : ""}`}
                        onClick={(e) => { pick(p, e.shiftKey || e.metaKey || e.ctrlKey); setFocus(p); }}>
                  <span className="disp-pin queue sm" style={{ "--c": "#ff9500" } as CSSProperties}>{p.type === "request" ? "?" : "!"}</span>
                  <div className="grow">
                    <b>{p.address}</b>
                    <div className="sub">
                      {[p.type === "request" ? `заявка №${p.id}` : `наряд №${p.id}`, p.client, p.date && dayLabel(p.date), p.window_start,
                        p.latitude == null ? "нет точки" : ""].filter(Boolean).join(" · ")}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
          {(board.unlocated.length > 0 || unlocatedQueue.length > 0) && (
            <>
              <div className="list-section">Без точки на карте <span className="count">{board.unlocated.length}</span></div>
              <div className="card-list">
                {board.unlocated.map((p) => (
                  <button type="button" key={keyOf(p)} className={`card tap disp-row ${selected.has(keyOf(p)) ? "active" : ""}`}
                          onClick={(e) => pick(p, e.shiftKey || e.metaKey || e.ctrlKey)}>
                    <span className="disp-pin sm" style={{ "--c": colors.get(p.employee_id ?? 0) } as CSSProperties}>{p.position}</span>
                    <div className="grow">
                      <b>{p.address}</b>
                      <div className="sub">{[p.employee, p.date && dayLabel(p.date), p.status_display].filter(Boolean).join(" · ")}</div>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
          {board.trips.length > 0 && (
            <>
              <div className="list-section">Командировки <span className="count">{board.trips.length}</span></div>
              <div className="card-list">
                {board.trips.map((t) => (
                  <button type="button" key={t.id} className="card tap disp-row" onClick={() => onOpenTrip(t.id)}>
                    <span className="dot" style={{ background: colors.get(t.lead_id) }} />
                    <div className="grow">
                      <b>{t.title}</b>
                      <div className="sub">
                        {t.lead}{t.member_ids.length ? ` +${t.member_ids.length}` : ""}
                        {t.date_start ? ` · ${dayLabel(t.date_start, { day: "numeric", month: "short" })}` : ""}
                        {t.date_end && t.date_end !== t.date_start ? `–${dayLabel(t.date_end, { day: "numeric", month: "short" })}` : ""}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
