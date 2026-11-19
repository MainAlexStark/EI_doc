import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { deleteAvailability, json, type AvailabilitySlot } from "../../api";
import {
  addDays, assignWork, dayLabel, fetchDispatchBoard, fetchTeamSchedule, initials, isoDay, teamColors,
  type MapPoint, type ScheduleCell, type TeamSchedule as Schedule,
} from "../../dispatchApi";
import Icon from "../../ui/Icon";
import { Modal } from "../../ui/Modal";
import { Alert, Segmented, Spinner } from "../../ui/controls";

/** Командный «График» руководства: сотрудники × дни.
 *
 * В клетке — когда человек может выезжать (слоты), сколько адресов у него в
 * этот день, командировка, задачи со сроком. Клик по клетке: поставить или
 * убрать слот и тут же отдать ему что-то из неназначенного на этот день. */

const QUICK: { label: string; start: string | null; end: string | null }[] = [
  { label: "Весь день", start: null, end: null },
  { label: "08–12", start: "08:00", end: "12:00" },
  { label: "13–17", start: "13:00", end: "17:00" },
  { label: "08–17", start: "08:00", end: "17:00" },
];

const createSlotFor = (employee: number, payload: { date: string; kind: "district" | "trip"; start_time: string | null; end_time: string | null }) =>
  json<AvailabilitySlot>("/api/hub/availability/", { method: "POST", body: JSON.stringify({ ...payload, employee }) });

const slotText = (s: { start: string | null; end: string | null }) => (s.start ? `${s.start.slice(0, 5)}–${(s.end ?? "").slice(0, 5)}` : "весь день");

function CellView({ cell }: { cell?: ScheduleCell }) {
  if (!cell) return null;
  return (
    <>
      {cell.trips.map((t) => <span key={t.id} className="cell-trip" title={t.title}>{t.title}</span>)}
      {cell.slots.map((s) => (
        <span key={s.id} className={`cell-slot ${s.kind} ${s.is_priority ? "prio" : ""}`}>{slotText(s)}</span>
      ))}
      {(cell.stops > 0 || cell.tasks > 0 || cell.lab > 0) && (
        <span className="cell-load">
          {cell.stops > 0 && <span title="Адресов"><Icon name="car" size={11} />{cell.done}/{cell.stops}</span>}
          {cell.lab > 0 && <span title="Приёмок"><Icon name="checkCircle" size={11} />{cell.lab}</span>}
          {cell.tasks > 0 && <span title="Задач со сроком"><Icon name="tasks" size={11} />{cell.tasks}</span>}
        </span>
      )}
    </>
  );
}

function CellSheet({
  employee, day, cell, color, onClose, onChanged,
}: {
  employee: { id: number; full_name: string; short_name: string };
  day: string;
  cell?: ScheduleCell;
  color?: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [queue, setQueue] = useState<MapPoint[] | null>(null);
  const [kind, setKind] = useState<"district" | "trip">("district");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<string[]>([]);
  const [from, setFrom] = useState("09:00");
  const [to, setTo] = useState("13:00");

  useEffect(() => {
    fetchDispatchBoard(day, day).then((b) => setQueue(b.queue)).catch(() => setQueue([]));
  }, [day]);

  const run = async (job: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await job();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось");
    } finally {
      setBusy(false);
    }
  };

  const addSlot = (start: string | null, end: string | null) =>
    run(() => createSlotFor(employee.id, { date: day, kind, start_time: start, end_time: end }));

  const give = (p: MapPoint) =>
    run(async () => {
      const result = await assignWork({ items: [{ type: p.type, id: p.id }], employee_id: employee.id, date: day, time: p.window_start });
      if (result.errors.length) throw new Error(result.errors[0].error);
      setDone((cur) => [...cur, `${p.type}:${p.id}`]);
    });

  const waiting = (queue ?? []).filter((p) => !done.includes(`${p.type}:${p.id}`));

  return (
    <Modal open onClose={onClose} size="sm" title={employee.full_name}
           subtitle={dayLabel(day, { weekday: "long", day: "numeric", month: "long" })}>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {cell?.trips.length ? (
        <div className="kv">
          {cell.trips.map((t) => <div key={t.id}><span className="k">Командировка</span><span className="v">{t.title}{t.is_lead ? " · старший" : ""}</span></div>)}
        </div>
      ) : null}
      <div className="field-label">Когда может выезжать</div>
      <div className="sheet-slots">
        {(cell?.slots ?? []).length === 0 && <span className="sub">Слотов нет — заявители на сайте этот день у него не видят.</span>}
        {(cell?.slots ?? []).map((s) => (
          <span key={s.id} className={`cell-slot ${s.kind} big`} style={{ "--vc": color } as CSSProperties}>
            {slotText(s)}{s.kind === "trip" ? " · командировка" : ""}
            <button type="button" className="icon-btn" aria-label="Убрать слот" disabled={busy}
                    onClick={() => void run(() => deleteAvailability(s.id))}><Icon name="x" size={12} /></button>
          </span>
        ))}
      </div>
      <div className="sheet-add">
        <Segmented size="sm" value={kind} onChange={setKind}
                   options={[{ value: "district", label: "В районе" }, { value: "trip", label: "Командировка" }]} />
        <div className="chip-row">
          {QUICK.map((q) => (
            <button type="button" key={q.label} className="chip" disabled={busy} onClick={() => void addSlot(q.start, q.end)}>
              <Icon name="plus" size={12} /> {q.label}
            </button>
          ))}
        </div>
        <div className="sheet-custom">
          <input type="time" step={1800} value={from} onChange={(e) => setFrom(e.target.value)} aria-label="С" />
          <span>–</span>
          <input type="time" step={1800} value={to} onChange={(e) => setTo(e.target.value)} aria-label="До" />
          <button type="button" disabled={busy || !from || !to || to <= from} onClick={() => void addSlot(from, to)}>Добавить</button>
        </div>
      </div>

      <div className="field-label" style={{ marginTop: 14 }}>Отдать ему на этот день</div>
      {queue == null ? <Spinner /> : waiting.length === 0 ? (
        <p className="hint">{done.length ? `Отдано: ${done.length}. Больше неназначенного нет.` : "Неназначенных заявок и выездов нет."}</p>
      ) : (
        <div className="card-list">
          {waiting.map((p) => (
            <div key={`${p.type}:${p.id}`} className="card disp-row">
              <div className="grow">
                <b>{p.address}</b>
                <div className="sub">{[p.type === "request" ? `заявка №${p.id}` : `наряд №${p.id}`, p.client,
                  p.date && p.date !== day ? `просили ${dayLabel(p.date)}` : "", p.window_start].filter(Boolean).join(" · ")}</div>
              </div>
              <button type="button" className="tinted sm" disabled={busy} onClick={() => void give(p)}>Отдать</button>
            </div>
          ))}
        </div>
      )}
      {cell && cell.stops > 0 && <p className="hint">Адресов в этот день: {cell.stops}, отработано {cell.done}. Порядок и переназначение — на карте «Выездов».</p>}
    </Modal>
  );
}

export default function TeamSchedule() {
  const monday = (() => {
    const d = new Date();
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return isoDay(d);
  })();
  const today = isoDay(new Date());
  const [from, setFrom] = useState(monday);
  const [span, setSpan] = useState<"7" | "14">("14");
  const [data, setData] = useState<Schedule | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<{ employee: number; day: string } | null>(null);

  const reload = useCallback(() => {
    fetchTeamSchedule(from, addDays(from, Number(span) - 1))
      .then(setData)
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить график"));
  }, [from, span]);
  useEffect(reload, [reload]);

  const colors = useMemo(() => teamColors((data?.employees ?? []).map((e) => e.id)), [data]);
  if (!data) return error ? <Alert>{error}</Alert> : <Spinner />;

  const openEmployee = open ? data.employees.find((e) => e.id === open.employee) : null;

  return (
    <div className="team-schedule">
      <div className="toolbar">
        <div className="date-nav">
          <button type="button" className="icon-btn" aria-label="Раньше" onClick={() => setFrom(addDays(from, -7))}><Icon name="chevronLeft" size={18} /></button>
          <button type="button" className={from === monday ? "tinted" : ""} onClick={() => setFrom(monday)}>Эта неделя</button>
          <button type="button" className="icon-btn" aria-label="Позже" onClick={() => setFrom(addDays(from, 7))}><Icon name="chevronRight" size={18} /></button>
        </div>
        <Segmented size="sm" value={span} onChange={setSpan} options={[{ value: "7", label: "Неделя" }, { value: "14", label: "2 недели" }]} />
        <span className="spacer" />
        <span className="toolbar-note">
          <span className="cell-slot district">слот</span> <span className="cell-slot trip">командировка</span> · клик по клетке — слот и назначение
        </span>
      </div>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      <div className="table-wrap">
        <table className="team-grid-table" style={{ "--days": data.days.length } as CSSProperties}>
          <thead>
            <tr>
              <th className="who">Сотрудник</th>
              {data.days.map((d) => {
                const wd = new Date(`${d}T12:00:00`).getDay();
                return (
                  <th key={d} className={`${d === today ? "today" : ""} ${wd === 0 || wd === 6 ? "weekend" : ""}`}>
                    {dayLabel(d, { weekday: "short" })}<b>{dayLabel(d, { day: "numeric" })}</b>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {data.employees.map((e) => (
              <tr key={e.id} style={{ "--vc": colors.get(e.id) } as CSSProperties}>
                <th className="who">
                  <span className="avatar sm" style={{ background: colors.get(e.id) }}>{initials(e.full_name)}</span>
                  <span><b>{e.short_name}</b><small>{e.position}</small></span>
                </th>
                {data.days.map((d) => {
                  const wd = new Date(`${d}T12:00:00`).getDay();
                  return (
                    <td key={d} className={`${d === today ? "today" : ""} ${wd === 0 || wd === 6 ? "weekend" : ""}`}>
                      <button type="button" className="cell" onClick={() => setOpen({ employee: e.id, day: d })}
                              aria-label={`${e.full_name}, ${dayLabel(d)}`}>
                        <CellView cell={e.cells[d]} />
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && openEmployee && (
        <CellSheet
          employee={openEmployee}
          day={open.day}
          cell={openEmployee.cells[open.day]}
          color={colors.get(openEmployee.id)}
          onClose={() => setOpen(null)}
          onChanged={reload}
        />
      )}
    </div>
  );
}
