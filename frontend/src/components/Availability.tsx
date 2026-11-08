/** «График» — личный календарь сотрудника в духе Google Calendar.
 *
 * Слои: когда я могу выезжать (в своём районе / командировка — их видит публичная форма заявки,
 * apps.hub.api_availability.AvailabilityPublicSlotsView), мои выезды (наряды) и мои задачи со сроком.
 * Руководитель и менеджер могут наложить графики сотрудников — как «Другие календари» в Google.
 * Свой слот заводится кликом или протяжкой по сетке; на несколько дней — протяжкой в месяце
 * или выбором дней в карточке слота.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  bulkCreateAvailability,
  deleteAvailability,
  fetchAvailabilityAll,
  fetchEmployees,
  fetchTaskCalendar,
  fetchWorkOrders,
  requestTelegramLinkCode,
  updateAvailability,
  updateTask,
  type AvailabilitySlot,
  type Employee,
  type Me,
  type Task,
  type TelegramLinkInfo,
  type WorkOrder,
} from "../api";
import Icon from "../ui/Icon";
import { confirmDialog, Modal } from "../ui/Modal";
import { Alert, PageActions, Segmented, Switch } from "../ui/controls";
import { PHONE, useMediaQuery, usePersistent } from "../ui/hooks";
import MonthCalendar from "./Calendar";
import CalendarView, {
  CalendarToolbar,
  fromIso,
  Popover,
  timeRange,
  toIso,
  visibleDays,
  type Anchor,
  type CalColor,
  type CalEvent,
  type CalSelection,
  type CalView,
} from "./CalendarView";

const VIEWS = ["day", "week", "month", "agenda"] as const;
const MANAGE_ANY = new Set(["owner", "head", "manager"]);
const TEAM_COLORS: CalColor[] = ["teal", "pink", "indigo", "brown", "orange", "red", "gray"];

const KIND_LABEL: Record<AvailabilitySlot["kind"], string> = {
  district: "Выезд в своём районе",
  trip: "Командировка",
};
const PRIORITY_LABEL: Record<string, string> = { low: "низкий", normal: "обычный", high: "высокий", urgent: "срочно" };
const STATUS_LABEL: Record<string, string> = { todo: "к выполнению", in_progress: "в работе", done: "выполнена", cancelled: "отменена" };

type Layers = { district: boolean; trip: boolean; orders: boolean; tasks: boolean };

type Item =
  | { type: "slot"; slot: AvailabilitySlot; mine: boolean }
  | { type: "order"; order: WorkOrder }
  | { type: "task"; task: Task };

type Panel =
  | { mode: "create"; selection: CalSelection; anchor: Anchor }
  | { mode: "edit"; slot: AvailabilitySlot; anchor: Anchor }
  | { mode: "view"; event: CalEvent; item: Item; anchor: Anchor };

const longDate = (iso: string) => {
  const text = fromIso(iso).toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const shortName = (full: string) => {
  const [last, ...rest] = full.split(/\s+/).filter(Boolean);
  return last ? `${last} ${rest.map((p) => `${p[0]}.`).join("")}` : full;
};
const hm = (t: string | null) => (t ? t.slice(0, 5) : null);
const plusHour = (t: string) => {
  const [h, m] = t.split(":").map(Number);
  return `${String(Math.min(23, h + 1)).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
};

export default function Availability({ me }: { me: Me | null }) {
  const phone = useMediaQuery(PHONE);
  const [view, setView] = usePersistent<CalView>("ei_doc_graph_view", phone ? "agenda" : "week", VIEWS);
  const [cursor, setCursor] = useState(() => new Date());
  const [layers, setLayers] = useState<Layers>({ district: true, trip: true, orders: true, tasks: true });
  const [slots, setSlots] = useState<AvailabilitySlot[]>([]);
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [team, setTeam] = useState<Record<number, boolean>>({});
  const [teamSlots, setTeamSlots] = useState<Record<number, AvailabilitySlot[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [telegramOpen, setTelegramOpen] = useState(false);

  const myId = me?.employee?.id ?? null;
  const canSeeTeam = MANAGE_ANY.has(me?.role ?? "");
  const days = useMemo(() => visibleDays(view, cursor), [view, cursor]);
  const from = days[0];
  const to = days[days.length - 1];

  useEffect(() => {
    if (canSeeTeam) void fetchEmployees().then(setEmployees).catch(() => undefined);
  }, [canSeeTeam]);

  const load = useCallback(async () => {
    if (!myId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [mySlots, myOrders, calendar] = await Promise.all([
        fetchAvailabilityAll({ date_from: from, date_to: to }),
        fetchWorkOrders({ assigned_employee: String(myId), date_from: from, date_to: to }).catch(() => [] as WorkOrder[]),
        fetchTaskCalendar(from, to).catch(() => ({}) as Record<string, Task[]>),
      ]);
      setSlots(mySlots);
      setOrders(myOrders);
      setTasks(Object.values(calendar).flat().filter((t) => t.assignee === myId));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить график");
    } finally {
      setLoading(false);
    }
  }, [from, to, myId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Графики сотрудников — только отмеченные.
  const teamKey = Object.entries(team).filter(([, on]) => on).map(([id]) => id).join(",");
  useEffect(() => {
    if (!teamKey) {
      setTeamSlots({});
      return;
    }
    let cancelled = false;
    const ids = teamKey.split(",").map(Number);
    void Promise.all(ids.map((id) => fetchAvailabilityAll({ employee: id, date_from: from, date_to: to }).then((list) => [id, list] as const)))
      .then((pairs) => !cancelled && setTeamSlots(Object.fromEntries(pairs)))
      .catch((exc) => !cancelled && setError(exc instanceof Error ? exc.message : "Не удалось загрузить график сотрудника"));
    return () => {
      cancelled = true;
    };
  }, [teamKey, from, to]);

  const others = employees.filter((e) => e.id !== myId);
  const colorOf = (employeeId: number) => TEAM_COLORS[Math.max(0, others.findIndex((e) => e.id === employeeId)) % TEAM_COLORS.length];

  const { events, items } = useMemo(() => {
    const list: CalEvent[] = [];
    const map = new Map<string, Item>();
    const add = (event: CalEvent, item: Item) => {
      list.push(event);
      map.set(event.id, item);
    };
    for (const slot of slots) {
      if (!layers[slot.kind]) continue;
      add(
        {
          id: `slot-${slot.id}`,
          date: slot.date,
          start: hm(slot.start_time),
          end: hm(slot.end_time),
          title: slot.kind === "trip" ? "Командировка" : "Могу выезжать",
          subtitle: [slot.is_priority ? "приоритетное время" : "", slot.note].filter(Boolean).join(" · "),
          color: slot.kind === "trip" ? "purple" : "blue",
          icon: slot.is_priority ? "star" : undefined,
        },
        { type: "slot", slot, mine: true },
      );
    }
    if (layers.orders) {
      for (const order of orders) {
        if (!order.scheduled_date || order.status === "cancelled") continue;
        const start = hm(order.scheduled_time);
        add(
          {
            id: `order-${order.id}`,
            date: order.scheduled_date,
            start,
            end: start ? plusHour(start) : null,
            title: order.site || `Выезд №${order.id}`,
            subtitle: order.client,
            color: "green",
            icon: "car",
            done: order.status === "done",
            chip: "solid",
          },
          { type: "order", order },
        );
      }
    }
    if (layers.tasks) {
      for (const task of tasks) {
        if (!task.due_date) continue;
        add(
          {
            id: `task-${task.id}`,
            date: task.due_date,
            title: task.title,
            subtitle: `Задача · ${PRIORITY_LABEL[task.priority] ?? task.priority}`,
            color: task.priority === "urgent" ? "red" : "orange",
            icon: "tasks",
            done: task.status === "done",
          },
          { type: "task", task },
        );
      }
    }
    for (const [id, list] of Object.entries(teamSlots)) {
      const employee = others.find((e) => e.id === Number(id));
      if (!employee || !team[Number(id)]) continue;
      for (const slot of list) {
        add(
          {
            id: `team-${slot.id}`,
            date: slot.date,
            start: hm(slot.start_time),
            end: hm(slot.end_time),
            title: shortName(employee.full_name),
            subtitle: `${KIND_LABEL[slot.kind]}${slot.note ? ` · ${slot.note}` : ""}`,
            color: colorOf(employee.id),
            icon: slot.kind === "trip" ? "briefcase" : slot.is_priority ? "star" : undefined,
          },
          { type: "slot", slot, mine: false },
        );
      }
    }
    return { events: list, items: map };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slots, orders, tasks, layers, teamSlots, team, employees]);

  const createAt = (anchor: Anchor, date = toIso(cursor)) =>
    setPanel({ mode: "create", selection: { dates: [date] }, anchor });

  const createButton = (el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const date = days.includes(toIso(new Date())) ? toIso(new Date()) : days[0];
    createAt({ left: r.left, top: r.bottom + 6, width: r.width, height: 0 }, view === "month" || view === "agenda" ? date : toIso(cursor));
  };

  const counts = {
    district: slots.filter((s) => s.kind === "district").length,
    trip: slots.filter((s) => s.kind === "trip").length,
    orders: orders.filter((o) => o.scheduled_date && o.status !== "cancelled").length,
    tasks: tasks.length,
  };

  if (!me) return null;
  if (!myId) {
    return (
      <div className="empty">Календарь доступен сотрудникам с привязанной учётной записью</div>
    );
  }

  return (
    <>
      <PageActions>
        <button type="button" className="icon-btn only-narrow" title="Уведомления в Telegram" aria-label="Уведомления в Telegram" onClick={() => setTelegramOpen(true)}>
          <Icon name="telegram" />
        </button>
        <button type="button" className="primary only-narrow" onClick={(e) => createButton(e.currentTarget)}>
          <Icon name="plus" size={16} strokeWidth={2.2} />
          Слот
        </button>
      </PageActions>

      <div className="gc-page">
        <aside className="gc-side">
          <button type="button" className="gc-create" onClick={(e) => createButton(e.currentTarget)}>
            <Icon name="plus" size={22} strokeWidth={2} />
            Создать
          </button>

          <MonthCalendar
            className="compact"
            selected={[toIso(cursor)]}
            onToggle={(date) => {
              setCursor(fromIso(date));
              if (view === "agenda") setView("week");
            }}
            minDate=""
            month={cursor}
            range={view === "week" ? days : undefined}
            showOutside
            markers={Object.fromEntries(slots.map((s) => [s.date, { priority: s.is_priority }]))}
          />

          <div>
            <div className="gc-side-title">Мои календари</div>
            <div className="gc-cal-list">
              <LayerItem color="blue" label="Выезды в районе" count={counts.district} checked={layers.district} onChange={(v) => setLayers({ ...layers, district: v })} />
              <LayerItem color="purple" label="Командировки" count={counts.trip} checked={layers.trip} onChange={(v) => setLayers({ ...layers, trip: v })} />
              <LayerItem color="green" label="Мои выезды (наряды)" count={counts.orders} checked={layers.orders} onChange={(v) => setLayers({ ...layers, orders: v })} />
              <LayerItem color="orange" label="Мои задачи" count={counts.tasks} checked={layers.tasks} onChange={(v) => setLayers({ ...layers, tasks: v })} />
            </div>
          </div>

          {canSeeTeam && others.length > 0 && (
            <div>
              <div className="gc-side-title">Сотрудники</div>
              <div className="gc-cal-list">
                {others.map((employee) => (
                  <LayerItem
                    key={employee.id}
                    color={colorOf(employee.id)}
                    label={shortName(employee.full_name)}
                    title={employee.full_name}
                    checked={Boolean(team[employee.id])}
                    onChange={(v) => setTeam({ ...team, [employee.id]: v })}
                  />
                ))}
              </div>
            </div>
          )}

          <div className="gc-side-card">
            <h4><Icon name="star" size={14} /> Приоритетное время</h4>
            <p>Слоты со звёздочкой заявитель на сайте видит со скидкой — так клиенты чаще выбирают удобное вам время.</p>
            <h4><Icon name="telegram" size={14} /> Telegram</h4>
            <p>{me.employee?.telegram_linked ? "Уведомления о нарядах и задачах приходят в Telegram." : "Подключите бота — наряды и задачи будут приходить в Telegram."}</p>
            <button type="button" className="sm" onClick={() => setTelegramOpen(true)}>
              {me.employee?.telegram_linked ? "Перепривязать" : "Подключить"}
            </button>
          </div>
        </aside>

        <section className="gc-main">
          <CalendarToolbar
            view={view}
            cursor={cursor}
            onCursor={setCursor}
            onView={setView}
            views={phone ? ["day", "month", "agenda"] : [...VIEWS]}
            loading={loading}
          />
          {error && <div style={{ padding: "10px 16px 0" }}><Alert onClose={() => setError("")}>{error}</Alert></div>}
          <div className="gc-view">
            <CalendarView
              view={view}
              cursor={cursor}
              events={events}
              pending={panel?.mode === "create" ? panel.selection : null}
              onCreate={(selection, anchor) => setPanel({ mode: "create", selection, anchor })}
              onOpen={(event, anchor) => {
                const item = items.get(event.id);
                if (item) setPanel({ mode: "view", event, item, anchor });
              }}
              onDay={(date) => {
                setCursor(fromIso(date));
                setView("day");
              }}
              emptyAgenda={
                <div className="empty-state">
                  <div className="empty-icon"><Icon name="calendar" size={26} /></div>
                  <div className="empty-title">На ближайший месяц ничего нет</div>
                  <div className="empty-text">Отметьте дни, когда можете выезжать, — заявители на сайте увидят их при выборе даты.</div>
                  <div className="empty-action">
                    <button type="button" className="primary" onClick={(e) => createButton(e.currentTarget)}>
                      <Icon name="plus" size={16} /> Добавить слот
                    </button>
                  </div>
                </div>
              }
            />
          </div>
        </section>
      </div>

      {panel?.mode === "create" && (
        <SlotEditor
          key={`create-${panel.selection.dates.join(",")}-${panel.selection.start ?? ""}`}
          anchor={panel.anchor}
          selection={panel.selection}
          existing={slots}
          onClose={() => setPanel(null)}
          onSaved={() => {
            setPanel(null);
            void load();
          }}
        />
      )}
      {panel?.mode === "edit" && (
        <SlotEditor
          key={`edit-${panel.slot.id}`}
          anchor={panel.anchor}
          slot={panel.slot}
          existing={slots}
          onClose={() => setPanel(null)}
          onSaved={() => {
            setPanel(null);
            void load();
          }}
        />
      )}
      {panel?.mode === "view" && (
        <EventCard
          panel={panel}
          onClose={() => setPanel(null)}
          onEdit={(slot) => setPanel({ mode: "edit", slot, anchor: panel.anchor })}
          onChanged={() => {
            setPanel(null);
            void load();
          }}
          onError={setError}
        />
      )}

      <TelegramLink open={telegramOpen} linked={Boolean(me.employee?.telegram_linked)} onClose={() => setTelegramOpen(false)} />
    </>
  );
}

function LayerItem({
  color, label, count, checked, onChange, title,
}: {
  color: CalColor;
  label: string;
  count?: number;
  checked: boolean;
  onChange: (value: boolean) => void;
  title?: string;
}) {
  return (
    <label className={`gc-cal-item ev-${color}`} title={title} style={{ ["--cal" as string]: "var(--ev-dot)" }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="gc-cal-box"><Icon name="check" size={12} strokeWidth={3} /></span>
      <span className="gc-cal-name">{label}</span>
      {count != null && count > 0 && <span className="gc-cal-count">{count}</span>}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Карточка события

function EventCard({
  panel, onClose, onEdit, onChanged, onError,
}: {
  panel: Extract<Panel, { mode: "view" }>;
  onClose: () => void;
  onEdit: (slot: AvailabilitySlot) => void;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const { event, item, anchor } = panel;
  const [busy, setBusy] = useState(false);

  const remove = async (slot: AvailabilitySlot) => {
    if (!(await confirmDialog({ title: "Удалить слот?", text: `${longDate(slot.date)}, ${timeRange({ start: hm(slot.start_time), end: hm(slot.end_time) })}`, danger: true }))) return;
    setBusy(true);
    try {
      await deleteAvailability(slot.id);
      onChanged();
    } catch (exc) {
      onError(exc instanceof Error ? exc.message : "Не удалось удалить слот");
    } finally {
      setBusy(false);
    }
  };

  const completeTask = async (task: Task) => {
    setBusy(true);
    try {
      await updateTask(task.id, { status: task.status === "done" ? "todo" : "done" });
      onChanged();
    } catch (exc) {
      onError(exc instanceof Error ? exc.message : "Не удалось изменить задачу");
    } finally {
      setBusy(false);
    }
  };

  const actions =
    item.type === "slot" && item.mine ? (
      <>
        <button type="button" className="icon-btn sm" title="Изменить" aria-label="Изменить" onClick={() => onEdit(item.slot)}>
          <Icon name="pen" size={16} />
        </button>
        <button type="button" className="icon-btn sm danger" title="Удалить" aria-label="Удалить" disabled={busy} onClick={() => void remove(item.slot)}>
          <Icon name="trash" size={16} />
        </button>
      </>
    ) : null;

  return (
    <Popover anchor={anchor} onClose={onClose} actions={actions}>
      <div className="ev-card">
        <div className="ev-card-title">
          <span className={`gc-swatch ev-${event.color}`} />
          <div>
            <h3>
              {item.type === "slot" ? (item.mine ? KIND_LABEL[item.slot.kind] : `${item.slot.employee_name} — ${KIND_LABEL[item.slot.kind].toLowerCase()}`) : event.title}
            </h3>
            <div className="sub">
              {longDate(event.date)} · {timeRange(event)}
            </div>
          </div>
        </div>

        {item.type === "slot" && (
          <>
            {item.slot.is_priority && (
              <div className="ev-card-row"><Icon name="star" size={16} /><span>Приоритетное время — заявителю показывается со скидкой</span></div>
            )}
            {item.slot.note && <div className="ev-card-row"><Icon name="doc" size={16} /><span>{item.slot.note}</span></div>}
            <div className="ev-card-row">
              <Icon name="globe" size={16} />
              <span>Виден заявителям в форме на сайте{item.slot.start_time ? "" : " — на весь день"}</span>
            </div>
          </>
        )}

        {item.type === "order" && (
          <>
            {item.order.client && <div className="ev-card-row"><Icon name="user" size={16} /><span>{item.order.client}</span></div>}
            <div className="ev-card-row"><Icon name="checkCircle" size={16} /><span>{item.order.status_display} · принято {item.order.verifications_accepted} из {item.order.verifications_total}</span></div>
            {item.order.note && <div className="ev-card-row"><Icon name="doc" size={16} /><span>{item.order.note}</span></div>}
            {item.order.request_id != null && <div className="ev-card-row"><Icon name="inbox" size={16} /><span>По заявке №{item.order.request_id}</span></div>}
            <div className="actions" style={{ marginTop: 4 }}>
              <button type="button" className="tinted" onClick={() => (window.location.hash = "#/field-work")}>
                Открыть в «Поверках» <Icon name="arrowRight" size={14} />
              </button>
            </div>
          </>
        )}

        {item.type === "task" && (
          <>
            <div className="ev-card-row"><Icon name="flag" size={16} /><span>Приоритет: {PRIORITY_LABEL[item.task.priority] ?? item.task.priority} · {STATUS_LABEL[item.task.status] ?? item.task.status}</span></div>
            {item.task.description && <div className="ev-card-row"><Icon name="doc" size={16} /><span>{item.task.description}</span></div>}
            {item.task.estimated_hours && <div className="ev-card-row"><Icon name="clock" size={16} /><span>Оценка: {item.task.estimated_hours} ч</span></div>}
            <div className="actions" style={{ marginTop: 4 }}>
              <button type="button" className={item.task.status === "done" ? "" : "primary"} disabled={busy} onClick={() => void completeTask(item.task)}>
                <Icon name={item.task.status === "done" ? "undo" : "check"} size={15} />
                {item.task.status === "done" ? "Вернуть в работу" : "Выполнено"}
              </button>
            </div>
          </>
        )}
      </div>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// Создание / правка слота

function SlotEditor({
  anchor, selection, slot, existing, onClose, onSaved,
}: {
  anchor: Anchor;
  selection?: CalSelection;
  slot?: AvailabilitySlot;
  existing: AvailabilitySlot[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = Boolean(slot);
  const [dates, setDates] = useState<string[]>(slot ? [slot.date] : selection?.dates ?? []);
  const [kind, setKind] = useState<AvailabilitySlot["kind"]>(slot?.kind ?? "district");
  const initialStart = slot ? hm(slot.start_time) : selection?.start ?? null;
  const [allDay, setAllDay] = useState(!initialStart);
  const [start, setStart] = useState(initialStart ?? "09:00");
  const [end, setEnd] = useState((slot ? hm(slot.end_time) : selection?.end) ?? "18:00");
  const [priority, setPriority] = useState(slot?.is_priority ?? false);
  const [note, setNote] = useState(slot?.note ?? "");
  const [pickDays, setPickDays] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const toggleDate = (date: string) =>
    setDates((current) => (current.includes(date) ? current.filter((d) => d !== date) : [...current, date].sort()));

  const save = async () => {
    if (dates.length === 0) {
      setError("Выберите хотя бы один день");
      return;
    }
    if (!allDay && start >= end) {
      setError("Время начала должно быть раньше окончания");
      return;
    }
    setBusy(true);
    setError("");
    const times = { start_time: allDay ? null : start, end_time: allDay ? null : end };
    try {
      if (slot) {
        await updateAvailability(slot.id, { kind, date: dates[0], ...times, is_priority: priority, note });
      } else {
        await bulkCreateAvailability({ dates, kind, ...times, is_priority: priority, note });
      }
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  const markers = Object.fromEntries(existing.map((s) => [s.date, { priority: s.is_priority }]));

  return (
    <Popover
      anchor={anchor}
      onClose={onClose}
      width={380}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Сохраняю…" : editing ? "Сохранить" : dates.length > 1 ? `Добавить на ${dates.length} дн.` : "Добавить"}
          </button>
        </>
      }
    >
      <form
        className="ev-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <h3 style={{ margin: "0 0 2px", fontSize: 17 }}>{editing ? "Слот графика" : "Когда могу выезжать"}</h3>
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { value: "district", label: "В своём районе", icon: "car" },
            { value: "trip", label: "Командировка", icon: "briefcase" },
          ]}
        />

        <div className="ev-card-row">
          <Icon name="calendar" size={16} />
          <div className="stack" style={{ gap: 6 }}>
            {dates.length === 1 ? (
              <span>{longDate(dates[0])}</span>
            ) : (
              <div className="ev-dates">
                {dates.slice(0, 8).map((d) => (
                  <span className="tag" key={d}>{fromIso(d).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "")}</span>
                ))}
                {dates.length > 8 && <span className="tag">+{dates.length - 8}</span>}
                {dates.length === 0 && <span className="muted">дни не выбраны</span>}
              </div>
            )}
            {!editing && (
              <button type="button" className="link" style={{ alignSelf: "flex-start" }} onClick={() => setPickDays((v) => !v)}>
                {pickDays ? "Готово" : "Выбрать несколько дней"}
              </button>
            )}
            {pickDays && <MonthCalendar selected={dates} onToggle={toggleDate} markers={markers} month={dates[0] ? fromIso(dates[0]) : undefined} />}
          </div>
        </div>

        <div className="ev-card-row">
          <Icon name="clock" size={16} />
          <div className="stack" style={{ gap: 8 }}>
            <Switch checked={allDay} onChange={setAllDay} label="Весь день" />
            {!allDay && (
              <div className="time-row">
                <input type="time" value={start} step={900} onChange={(e) => setStart(e.target.value)} aria-label="С" />
                <span className="muted">—</span>
                <input type="time" value={end} step={900} onChange={(e) => setEnd(e.target.value)} aria-label="По" />
              </div>
            )}
          </div>
        </div>

        <div className="ev-card-row">
          <Icon name="star" size={16} />
          <Switch checked={priority} onChange={setPriority} label="Приоритетное для меня" hint="Заявитель увидит это время со скидкой" />
        </div>

        <div className="ev-card-row">
          <Icon name="doc" size={16} />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Примечание — например, район или «после обеда»" />
        </div>
        {error && <Alert>{error}</Alert>}
        <button type="submit" hidden />
      </form>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// Привязка Telegram

function TelegramLink({ open, linked, onClose }: { open: boolean; linked: boolean; onClose: () => void }) {
  const [info, setInfo] = useState<TelegramLinkInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const request = async () => {
    setBusy(true);
    setError("");
    try {
      setInfo(await requestTelegramLinkCode());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось получить код");
    } finally {
      setBusy(false);
    }
  };

  const command = info ? `/start ${info.code}` : "";
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="sm"
      title="Уведомления в Telegram"
      subtitle={linked ? "Telegram уже подключён" : "Новые наряды и задачи — сразу в Telegram"}
      footer={
        info ? (
          <>
            <button type="button" onClick={() => void request()} disabled={busy}>Новый код</button>
            <button type="button" className="primary" onClick={onClose}>Готово</button>
          </>
        ) : (
          <>
            <button type="button" onClick={onClose}>Закрыть</button>
            <button type="button" className="primary" disabled={busy} onClick={() => void request()}>
              {busy ? "Получаю…" : linked ? "Перепривязать" : "Получить код"}
            </button>
          </>
        )
      }
    >
      {!info && (
        <p className="dialog-text">
          Бот пришлёт уведомление, когда на вас назначат выезд или задачу. Привязка — одноразовым кодом: получите его
          здесь и отправьте боту.
        </p>
      )}
      {info && (
        <div className="stack" style={{ gap: 12 }}>
          <p className="dialog-text">
            Откройте {info.bot_username ? <a href={`https://t.me/${info.bot_username}`} target="_blank" rel="noreferrer">@{info.bot_username}</a> : "бота EI_doc"} в Telegram и отправьте сообщение:
          </p>
          <div className="row" style={{ background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px" }}>
            <code style={{ fontSize: 16, flex: 1, letterSpacing: "0.04em", userSelect: "all" }}>{command}</code>
            <button
              type="button"
              className="sm"
              onClick={() => {
                void navigator.clipboard?.writeText(command).then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                });
              }}
            >
              <Icon name={copied ? "check" : "copy"} size={14} /> {copied ? "Скопировано" : "Копировать"}
            </button>
          </div>
          <span className="hint">
            Код действует до {new Date(info.expires_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}.
            {info.already_linked && " Новый код перепривяжет чат заново."}
          </span>
        </div>
      )}
      {error && <Alert>{error}</Alert>}
    </Modal>
  );
}
