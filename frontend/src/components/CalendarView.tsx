/** Большой календарь в духе Google Calendar: день / неделя / месяц / расписание.
 *
 * Сам ничего не грузит и не хранит — получает события и отдаёт наверх намерения:
 * «создать на этих датах/в это время» (клик или протяжка по сетке), «открыть событие».
 * Используется в «Графике» (доступность, выезды, задачи) и в «Задачах» (календарь сроков).
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon, { type IconName } from "../ui/Icon";

export type CalColor = "blue" | "purple" | "green" | "orange" | "red" | "teal" | "gray" | "pink" | "indigo" | "brown";
export type CalView = "day" | "week" | "month" | "agenda";

export type CalEvent = {
  id: string;
  date: string;
  /** "HH:MM" — без времени событие идёт строкой «весь день». */
  start?: string | null;
  end?: string | null;
  title: string;
  subtitle?: string;
  color: CalColor;
  icon?: IconName;
  done?: boolean;
  /** Как рисовать в месяце: мягкая плашка (по умолчанию для «весь день»), сплошная, точка. */
  chip?: "soft" | "solid" | "dot";
};

export type CalSelection = { dates: string[]; start?: string; end?: string };
export type Anchor = { left: number; top: number; width: number; height: number };

const HOUR_PX = 48;
const SNAP_MIN = 15;
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

// ---------------------------------------------------------------------------
// даты

export function toIso(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function fromIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function startOfWeek(date: Date): Date {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return addDays(d, -((d.getDay() + 6) % 7));
}

export const todayIso = () => toIso(new Date());

const AGENDA_DAYS = 30;

/** Дни, которые видны в этом виде, — для загрузки данных и для сетки. */
export function visibleDays(view: CalView, cursor: Date): string[] {
  let first: Date;
  let count: number;
  if (view === "day") {
    first = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate());
    count = 1;
  } else if (view === "week") {
    first = startOfWeek(cursor);
    count = 7;
  } else if (view === "month") {
    first = startOfWeek(new Date(cursor.getFullYear(), cursor.getMonth(), 1));
    const last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0);
    const end = addDays(startOfWeek(last), 6);
    count = Math.round((end.getTime() - first.getTime()) / 86_400_000) + 1;
  } else {
    first = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate());
    count = AGENDA_DAYS;
  }
  return Array.from({ length: count }, (_, i) => toIso(addDays(first, i)));
}

export function shiftCursor(view: CalView, cursor: Date, direction: 1 | -1): Date {
  if (view === "day") return addDays(cursor, direction);
  if (view === "week") return addDays(cursor, 7 * direction);
  if (view === "agenda") return addDays(cursor, AGENDA_DAYS * direction);
  return new Date(cursor.getFullYear(), cursor.getMonth() + direction, 1);
}

const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export function viewTitle(view: CalView, cursor: Date): string {
  return cap(rawTitle(view, cursor));
}

function rawTitle(view: CalView, cursor: Date): string {
  if (view === "day") {
    return cursor.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  }
  if (view === "month") return cursor.toLocaleDateString("ru-RU", { month: "long", year: "numeric" }).replace(" г.", "");
  const days = visibleDays(view, cursor);
  const a = fromIso(days[0]);
  const b = fromIso(days[days.length - 1]);
  if (a.getMonth() === b.getMonth()) {
    return `${a.getDate()} – ${b.getDate()} ${b.toLocaleDateString("ru-RU", { month: "short", year: "numeric" })}`.replace(" г.", "");
  }
  const left = a.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  const right = b.toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
  return `${left} – ${right}`.replace(" г.", "");
}

const minutes = (time: string) => {
  const [h, m] = time.split(":").map(Number);
  return h * 60 + (m || 0);
};
const hhmm = (total: number) => {
  const clamped = Math.max(0, Math.min(24 * 60, total));
  const h = Math.floor(clamped / 60);
  return `${String(h === 24 ? 23 : h).padStart(2, "0")}:${String(h === 24 ? 59 : clamped % 60).padStart(2, "0")}`;
};
export const timeRange = (event: Pick<CalEvent, "start" | "end">) =>
  event.start ? `${event.start.slice(0, 5)}${event.end ? `–${event.end.slice(0, 5)}` : ""}` : "весь день";

const rectOf = (el: Element): Anchor => {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
};

const byTime = (a: CalEvent, b: CalEvent) => {
  if (!a.start && b.start) return -1;
  if (a.start && !b.start) return 1;
  return (a.start ?? "").localeCompare(b.start ?? "") || a.title.localeCompare(b.title);
};

// ---------------------------------------------------------------------------

type Props = {
  view: CalView;
  cursor: Date;
  events: CalEvent[];
  /** Пустое место в сетке: клик или протяжка. Без обработчика сетка только для просмотра. */
  onCreate?: (selection: CalSelection, anchor: Anchor) => void;
  onOpen?: (event: CalEvent, anchor: Anchor) => void;
  /** Клик по числу дня — открыть этот день. */
  onDay?: (date: string) => void;
  /** Подсветка выбранного под создание (пока открыта карточка). */
  pending?: CalSelection | null;
  /** Час, к которому прокрутить сетку при открытии. */
  scrollHour?: number;
  emptyAgenda?: ReactNode;
};

export default function CalendarView(props: Props) {
  if (props.view === "month") return <MonthGrid {...props} />;
  if (props.view === "agenda") return <Agenda {...props} />;
  return <TimeGrid {...props} />;
}

// ---------------------------------------------------------------------------
// День / неделя

type Placed = { event: CalEvent; top: number; height: number; col: number; cols: number };

function layoutDay(events: CalEvent[]): Placed[] {
  const timed = events
    .filter((e) => e.start)
    .map((event) => {
      const s = minutes(event.start!);
      const e = event.end ? Math.max(minutes(event.end), s + 20) : s + 60;
      return { event, s, e };
    })
    .sort((a, b) => a.s - b.s || b.e - a.e);
  const placed: Placed[] = [];
  let cluster: { event: CalEvent; s: number; e: number; col: number }[] = [];
  let clusterEnd = -1;
  const flush = () => {
    const cols = Math.max(1, ...cluster.map((c) => c.col + 1));
    for (const c of cluster) {
      placed.push({ event: c.event, top: (c.s / 60) * HOUR_PX, height: Math.max(((c.e - c.s) / 60) * HOUR_PX, 18), col: c.col, cols });
    }
    cluster = [];
  };
  for (const item of timed) {
    if (item.s >= clusterEnd && cluster.length) flush();
    const used = new Set(cluster.filter((c) => c.e > item.s).map((c) => c.col));
    let col = 0;
    while (used.has(col)) col += 1;
    cluster.push({ ...item, col });
    clusterEnd = Math.max(clusterEnd, item.e);
  }
  if (cluster.length) flush();
  return placed;
}

function useNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function TimeGrid({ view, cursor, events, onCreate, onOpen, onDay, pending, scrollHour = 8 }: Props) {
  const days = visibleDays(view, cursor);
  const today = todayIso();
  const now = useNow();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ date: string; from: number; to: number } | null>(null);
  const [allDayDrag, setAllDayDrag] = useState<{ from: string; to: string } | null>(null);

  const byDay = useMemo(() => {
    const map = new Map<string, CalEvent[]>();
    for (const e of events) {
      const list = map.get(e.date) ?? [];
      list.push(e);
      map.set(e.date, list);
    }
    for (const list of map.values()) list.sort(byTime);
    return map;
  }, [events]);

  // Прокрутка к рабочему утру (или к первому событию, если оно раньше) — один раз на вид.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const earliest = events.filter((e) => e.start && days.includes(e.date)).map((e) => minutes(e.start!) / 60);
    const hour = Math.max(0, Math.min(scrollHour, ...earliest) - 0.5);
    el.scrollTop = hour * HOUR_PX;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, days[0]]);

  const minuteAt = (col: HTMLElement, clientY: number) => {
    const rect = col.getBoundingClientRect();
    const raw = ((clientY - rect.top) / HOUR_PX) * 60;
    return Math.max(0, Math.min(24 * 60 - SNAP_MIN, Math.floor(raw / SNAP_MIN) * SNAP_MIN));
  };

  const startDrag = (event: React.PointerEvent<HTMLDivElement>, date: string) => {
    if (!onCreate || event.button !== 0) return;
    if ((event.target as HTMLElement).closest(".gc-event")) return;
    const col = event.currentTarget;
    const from = minuteAt(col, event.clientY);
    let to = from + SNAP_MIN;
    let moved = false;
    setDrag({ date, from, to });
    const touch = event.pointerType === "touch";
    const move = (e: PointerEvent) => {
      if (touch) return; // на телефоне протяжка — это прокрутка, создаём по касанию
      const at = minuteAt(col, e.clientY) + SNAP_MIN;
      if (at !== to) moved = true;
      to = at;
      setDrag({ date, from, to });
    };
    const cancel = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      setDrag(null);
    };
    const up = () => {
      cancel();
      let start = Math.min(from, to - SNAP_MIN);
      let end = Math.max(from + SNAP_MIN, to);
      if (!moved) {
        start = Math.floor(from / 30) * 30;
        end = start + 60;
      }
      const rect = col.getBoundingClientRect();
      onCreate(
        { dates: [date], start: hhmm(start), end: hhmm(Math.min(end, 24 * 60 - 1)) },
        { left: rect.left, top: rect.top + (start / 60) * HOUR_PX, width: rect.width, height: ((end - start) / 60) * HOUR_PX },
      );
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  };

  const startAllDay = (event: React.PointerEvent<HTMLDivElement>, date: string) => {
    if (!onCreate || event.button !== 0) return;
    if ((event.target as HTMLElement).closest(".gc-chip, .gc-more")) return;
    event.preventDefault();
    let to = date;
    setAllDayDrag({ from: date, to });
    const move = (e: PointerEvent) => {
      const cell = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-allday]");
      if (cell?.dataset.allday && cell.dataset.allday !== to) {
        to = cell.dataset.allday;
        setAllDayDrag({ from: date, to });
      }
    };
    const cancel = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      setAllDayDrag(null);
    };
    const up = (e: PointerEvent) => {
      cancel();
      const [a, b] = date <= to ? [date, to] : [to, date];
      const picked = days.filter((d) => d >= a && d <= b);
      const cell = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-allday]") ?? event.currentTarget;
      onCreate({ dates: picked }, rectOf(cell));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  };

  const inAllDayDrag = (date: string) => {
    if (!allDayDrag) return pending && !pending.start ? pending.dates.includes(date) : false;
    const [a, b] = allDayDrag.from <= allDayDrag.to ? [allDayDrag.from, allDayDrag.to] : [allDayDrag.to, allDayDrag.from];
    return date >= a && date <= b;
  };

  const ghost = (date: string) => {
    if (drag && drag.date === date) {
      const s = Math.min(drag.from, drag.to - SNAP_MIN);
      const e = Math.max(drag.from + SNAP_MIN, drag.to);
      return { s, e };
    }
    if (!drag && pending?.start && pending.end && pending.dates.includes(date)) {
      return { s: minutes(pending.start), e: minutes(pending.end) };
    }
    return null;
  };

  const nowMin = now.getHours() * 60 + now.getMinutes();

  return (
    <div className="gc-week" style={{ ["--days" as string]: days.length, ["--hour" as string]: `${HOUR_PX}px` }}>
      <div className="gc-week-head">
        <div />
        {days.map((date) => {
          const d = fromIso(date);
          const weekend = d.getDay() === 0 || d.getDay() === 6;
          return (
            <div key={date} className={`gc-day-head ${date === today ? "today" : ""} ${weekend ? "weekend" : ""}`}>
              <span className="dow">{WEEKDAYS[(d.getDay() + 6) % 7]}</span>
              <button type="button" className="dnum" onClick={() => onDay?.(date)} title="Открыть день">
                {d.getDate()}
              </button>
            </div>
          );
        })}
      </div>
      <div className="gc-allday">
        <div className="gc-allday-label">весь день</div>
        {days.map((date) => (
          <div
            key={date}
            data-allday={date}
            className={`gc-allday-cell ${inAllDayDrag(date) ? "selecting" : ""}`}
            onPointerDown={(e) => startAllDay(e, date)}
          >
            {(byDay.get(date) ?? []).filter((e) => !e.start).map((event) => (
              <EventChip key={event.id} event={event} onOpen={onOpen} />
            ))}
          </div>
        ))}
      </div>
      <div className="gc-grid-scroll" ref={scrollRef}>
        <div className="gc-grid" style={{ height: 24 * HOUR_PX }}>
          <div className="gc-gutter">
            {Array.from({ length: 23 }, (_, i) => i + 1).map((h) => (
              <span key={h} className="gc-hour-label" style={{ top: h * HOUR_PX }}>
                {String(h).padStart(2, "0")}:00
              </span>
            ))}
          </div>
          {days.map((date) => {
            const d = fromIso(date);
            const weekend = d.getDay() === 0 || d.getDay() === 6;
            const g = ghost(date);
            return (
              <div
                key={date}
                className={`gc-col ${weekend ? "weekend" : ""} ${date === today ? "today" : ""}`}
                onPointerDown={(e) => startDrag(e, date)}
                style={{ cursor: onCreate ? undefined : "default" }}
              >
                {layoutDay(byDay.get(date) ?? []).map(({ event, top, height, col, cols }) => {
                  const short = height < 38;
                  return (
                    <button
                      type="button"
                      key={event.id}
                      className={`gc-event ev-${event.color} ${short ? "short" : ""} ${event.done ? "done" : ""}`}
                      style={{
                        top,
                        height: height - 2,
                        left: `calc(${(col / cols) * 100}% + 2px)`,
                        width: `calc(${100 / cols}% - ${cols > 1 ? 3 : 6}px)`,
                      }}
                      onClick={(e) => onOpen?.(event, rectOf(e.currentTarget))}
                      title={`${event.title} · ${timeRange(event)}`}
                    >
                      <span className="ev-title">
                        {event.icon && <Icon name={event.icon} size={11} strokeWidth={2.2} />}
                        {event.title}
                      </span>
                      <span className="ev-time">{timeRange(event)}</span>
                      {!short && height > 56 && event.subtitle && <span className="ev-sub">{event.subtitle}</span>}
                    </button>
                  );
                })}
                {g && (
                  <div className="gc-event ghost" style={{ top: (g.s / 60) * HOUR_PX, height: ((g.e - g.s) / 60) * HOUR_PX - 2 }}>
                    <span className="ev-title">Новый слот</span>
                    <span className="ev-time">{hhmm(g.s)}–{hhmm(g.e)}</span>
                  </div>
                )}
                {date === today && <div className="gc-now" style={{ top: (nowMin / 60) * HOUR_PX }} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function EventChip({ event, onOpen, month }: { event: CalEvent; onOpen?: Props["onOpen"]; month?: boolean }) {
  const style = event.chip ?? (event.start && month ? "dot" : "soft");
  return (
    <button
      type="button"
      className={`gc-chip ev-${event.color} ${style === "solid" ? "solid" : style === "dot" ? "dotted" : ""} ${event.done ? "done" : ""}`}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onOpen?.(event, rectOf(e.currentTarget));
      }}
      title={`${event.title} · ${timeRange(event)}${event.subtitle ? ` · ${event.subtitle}` : ""}`}
    >
      {month && event.start && <span className="ev-time">{event.start.slice(0, 5)}</span>}
      <span className="ev-title">
        {event.icon && <Icon name={event.icon} size={11} strokeWidth={2.2} />}
        {event.title}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Месяц

function MonthGrid({ cursor, events, onCreate, onOpen, onDay, pending }: Props) {
  const days = visibleDays("month", cursor);
  const today = todayIso();
  const month = cursor.getMonth();
  const [sel, setSel] = useState<{ from: string; to: string } | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const [perCell, setPerCell] = useState(3);

  // Сколько плашек помещается в ячейку — от фактической высоты строки.
  useLayoutEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const measure = () => {
      const cell = el.querySelector<HTMLElement>(".gc-mcell");
      if (!cell) return;
      const chip = window.matchMedia("(max-width: 760px)").matches ? 18 : 22;
      setPerCell(Math.max(1, Math.floor((cell.clientHeight - 34) / chip)));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [days.length]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalEvent[]>();
    for (const e of events) {
      const list = map.get(e.date) ?? [];
      list.push(e);
      map.set(e.date, list);
    }
    for (const list of map.values()) list.sort(byTime);
    return map;
  }, [events]);

  const start = (event: React.PointerEvent<HTMLDivElement>, date: string) => {
    if (!onCreate || event.button !== 0) return;
    if ((event.target as HTMLElement).closest(".gc-chip, .gc-more, .gc-mnum")) return;
    let to = date;
    setSel({ from: date, to });
    const touch = event.pointerType === "touch";
    const move = (e: PointerEvent) => {
      if (touch) return;
      const cell = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-day]");
      if (cell?.dataset.day && cell.dataset.day !== to) {
        to = cell.dataset.day;
        setSel({ from: date, to });
      }
    };
    const cancel = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      setSel(null);
    };
    const up = (e: PointerEvent) => {
      cancel();
      const [a, b] = date <= to ? [date, to] : [to, date];
      const cell = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-day]") ?? event.currentTarget;
      onCreate({ dates: days.filter((d) => d >= a && d <= b) }, rectOf(cell));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  };

  const selected = (date: string) => {
    if (sel) {
      const [a, b] = sel.from <= sel.to ? [sel.from, sel.to] : [sel.to, sel.from];
      return date >= a && date <= b;
    }
    return pending ? pending.dates.includes(date) : false;
  };

  return (
    <div className="gc-month">
      <div className="gc-month-head">
        {WEEKDAYS.map((d) => <span key={d}>{d}</span>)}
      </div>
      <div className="gc-month-grid" ref={gridRef} style={{ gridTemplateRows: `repeat(${days.length / 7}, minmax(0, 1fr))` }}>
        {days.map((date) => {
          const d = fromIso(date);
          const list = byDay.get(date) ?? [];
          const fits = list.length > perCell ? perCell - 1 : perCell;
          const hidden = list.length - fits;
          return (
            <div
              key={date}
              data-day={date}
              className={[
                "gc-mcell",
                d.getMonth() !== month ? "outside" : "",
                date === today ? "today" : "",
                date < today ? "past" : "",
                selected(date) ? "selecting" : "",
              ].join(" ")}
              onPointerDown={(e) => start(e, date)}
            >
              <button type="button" className="gc-mnum" onClick={() => onDay?.(date)} title="Открыть день">
                {d.getDate() === 1 ? d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "") : d.getDate()}
              </button>
              {list.slice(0, Math.max(0, fits)).map((event) => (
                <EventChip key={event.id} event={event} onOpen={onOpen} month />
              ))}
              {hidden > 0 && (
                <button
                  type="button"
                  className="gc-more"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => onDay?.(date)}
                >
                  ещё {hidden}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Расписание (список) — основной вид на телефоне

function Agenda({ cursor, events, onOpen, onDay, emptyAgenda }: Props) {
  const days = visibleDays("agenda", cursor);
  const today = todayIso();
  const groups = days
    .map((date) => ({ date, list: events.filter((e) => e.date === date).sort(byTime) }))
    .filter((g) => g.list.length > 0);
  if (groups.length === 0) {
    return <div className="gc-agenda">{emptyAgenda ?? <div className="empty">На ближайшие 30 дней ничего нет</div>}</div>;
  }
  return (
    <div className="gc-agenda">
      {groups.map(({ date, list }) => {
        const d = fromIso(date);
        return (
          <div key={date} className={`gc-agenda-day ${date === today ? "today" : ""}`}>
            <div className="gc-agenda-date" onClick={() => onDay?.(date)} role="button" tabIndex={-1}>
              <span className="dnum">{d.getDate()}</span>
              <span className="dow">
                {d.toLocaleDateString("ru-RU", { month: "short" }).replace(".", "")}, {WEEKDAYS[(d.getDay() + 6) % 7]}
              </span>
            </div>
            <div className="gc-agenda-events">
              {list.map((event) => (
                <button
                  type="button"
                  key={event.id}
                  className={`gc-agenda-row ev-${event.color} ${event.done ? "done" : ""}`}
                  onClick={(e) => onOpen?.(event, rectOf(e.currentTarget))}
                >
                  <span className="dot" />
                  <span className="time">{timeRange(event)}</span>
                  <span className="what">
                    <b>
                      {event.icon && <Icon name={event.icon} size={12} strokeWidth={2.2} />}
                      {event.title}
                    </b>
                    {event.subtitle && <span>{event.subtitle}</span>}
                  </span>
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Поповер у события / выделения. На телефоне — лист снизу (см. styles.css).

export function Popover({
  anchor, onClose, children, actions, footer, width = 360,
}: {
  anchor: Anchor;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const gap = 10;
    let left = anchor.left + anchor.width + gap;
    if (left + width > vw - 12) left = anchor.left - width - gap;
    if (left < 12) left = Math.max(12, Math.min(vw - width - 12, anchor.left + anchor.width / 2 - width / 2));
    let top = anchor.top;
    if (top + h > vh - 12) top = vh - h - 12;
    setPos({ left, top: Math.max(12, top) });
  }, [anchor, width]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return createPortal(
    <>
      <div className="popover-backdrop" onPointerDown={() => onClose()} />
      <div
        ref={ref}
        className="popover"
        role="dialog"
        style={{ width, left: pos?.left ?? -9999, top: pos?.top ?? -9999 }}
      >
        <div className="popover-head">
          {actions}
          <button type="button" className="icon-btn sm" onClick={onClose} aria-label="Закрыть">
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="popover-body">{children}</div>
        {footer && <div className="popover-foot">{footer}</div>}
      </div>
    </>,
    document.body,
  );
}

/** Панель над календарём: «Сегодня», стрелки, заголовок периода, выбор вида. */
export function CalendarToolbar({
  view, cursor, onCursor, onView, views = ["day", "week", "month", "agenda"], loading, extra,
}: {
  view: CalView;
  cursor: Date;
  onCursor: (date: Date) => void;
  onView: (view: CalView) => void;
  views?: CalView[];
  loading?: boolean;
  extra?: ReactNode;
}) {
  const labels: Record<CalView, string> = { day: "День", week: "Неделя", month: "Месяц", agenda: "Список" };
  return (
    <div className="gc-toolbar">
      <button type="button" className="today-btn" onClick={() => onCursor(new Date())}>Сегодня</button>
      <button type="button" className="icon-btn" aria-label="Назад" title="Назад" onClick={() => onCursor(shiftCursor(view, cursor, -1))}>
        <Icon name="chevronLeft" />
      </button>
      <button type="button" className="icon-btn" aria-label="Вперёд" title="Вперёд" onClick={() => onCursor(shiftCursor(view, cursor, 1))}>
        <Icon name="chevronRight" />
      </button>
      <span className="gc-title">{viewTitle(view, cursor)}</span>
      <span className="gc-loading">{loading && <span className="spinner sm" />}</span>
      <span className="spacer" />
      {extra}
      <div className="segmented sm" role="tablist" aria-label="Вид календаря">
        {views.map((v) => (
          <button type="button" role="tab" key={v} aria-selected={view === v} className={view === v ? "active" : ""} onClick={() => onView(v)}>
            {labels[v]}
          </button>
        ))}
      </div>
    </div>
  );
}
