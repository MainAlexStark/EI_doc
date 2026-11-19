import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  addDays, assignWork, dayLabel, fetchOverview, initials, isoDay, teamColors,
  type Attention, type Overview, type TeamCard,
} from "../../dispatchApi";
import Icon, { type IconName } from "../../ui/Icon";
import { Alert, EmptyState, PageActions, Spinner } from "../../ui/controls";
import AssignPicker from "./AssignPicker";

/** «Панель» — пульт старшего метролога, менеджера и директора.
 *
 * Вся работа всех сотрудников на один день: сверху цифры по фирме (каждая
 * ведёт в свой раздел), справа — что ждёт решения (заявки без исполнителя,
 * выезды без даты, готовые к выдаче приёмки, просроченные задачи) с быстрым
 * назначением прямо здесь, слева — карточка каждого: где он, сколько адресов
 * и что отработано, что висит в приёмках, черновиках и на нормоконтроле. */

export type Go = (tab: string, trip?: number) => void;

const STATE: Record<TeamCard["state"], { label: string; tone: string }> = {
  trip: { label: "Командировка", tone: "purple" },
  city: { label: "Выезды", tone: "info" },
  free: { label: "Свободен", tone: "ok" },
  off: { label: "Нет в графике", tone: "muted" },
};

const ATTENTION_ICON: Record<Attention["type"], IconName> = {
  request: "inbox", order: "mapPin", issue: "checkCircle", task: "alert",
};

function Kpi({ icon, value, label, tone, onClick }: { icon: IconName; value: string | number; label: string; tone?: string; onClick?: () => void }) {
  return (
    <button type="button" className={`kpi ${tone ?? ""}`} onClick={onClick} disabled={!onClick}>
      <Icon name={icon} size={18} />
      <b>{value}</b>
      <span>{label}</span>
    </button>
  );
}

export default function ControlPanel({ onGo }: { onGo: Go }) {
  const today = isoDay(new Date());
  const [date, setDate] = useState(today);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<"all" | TeamCard["state"]>("all");

  const reload = useCallback(() => {
    setError("");
    fetchOverview(date).then(setData).catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить панель"));
  }, [date]);

  useEffect(() => {
    reload();
    const timer = window.setInterval(reload, 60_000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const colors = useMemo(() => teamColors((data?.employees ?? []).map((e) => e.id)), [data]);

  if (error && !data) return <Alert>{error}</Alert>;
  if (!data) return <Spinner label="Собираю работу всех сотрудников…" />;

  const t = data.totals;
  const people = data.employees.map((e) => ({
    id: e.id, name: e.short_name, color: colors.get(e.id),
    hint: e.state === "trip" ? "в командировке" : e.stops.total ? `${e.stops.total} адр.` : e.slots.length ? "свободен" : "",
    muted: e.state === "off",
  }));

  const assign = async (item: Attention, employeeId: number, day: string | null, time: string | null) => {
    setBusy(true);
    setError("");
    try {
      const result = await assignWork({ items: [{ type: item.type as "request" | "order", id: item.id }], employee_id: employeeId, date: day, time });
      if (result.errors.length) setError(result.errors.map((e) => e.error).join("; "));
      else setNotice(result.done.map((d) => d.text).join("; "));
      setOpen(null);
      reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось назначить");
    } finally {
      setBusy(false);
    }
  };

  const shown = data.employees.filter((e) => filter === "all" || e.state === filter);
  const isToday = date === today;

  return (
    <div className="control">
      <PageActions>
        <div className="date-nav">
          <button type="button" className="icon-btn" aria-label="Предыдущий день" onClick={() => setDate(addDays(date, -1))}>
            <Icon name="chevronLeft" size={18} />
          </button>
          <button type="button" className={isToday ? "tinted" : ""} onClick={() => setDate(today)}>
            {isToday ? "Сегодня" : dayLabel(date, { weekday: "short", day: "numeric", month: "long" })}
          </button>
          <button type="button" className="icon-btn" aria-label="Следующий день" onClick={() => setDate(addDays(date, 1))}>
            <Icon name="chevronRight" size={18} />
          </button>
        </div>
        <button type="button" className="icon-btn" aria-label="Обновить" onClick={reload}>
          <Icon name="refresh" size={17} />
        </button>
      </PageActions>

      {notice && <Alert tone="ok" onClose={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      <div className="kpi-row">
        <Kpi icon="car" value={`${t.stops_done}/${t.stops_total}`} label={isToday ? "адресов сегодня" : "адресов за день"} onClick={() => onGo("trips")} />
        <Kpi icon="inbox" value={t.requests_new} label="заявок без исполнителя" tone={t.requests_new ? "warn" : ""} onClick={() => onGo("requests")} />
        <Kpi icon="mapPin" value={t.unscheduled} label="выездов без даты" tone={t.unscheduled ? "warn" : ""} onClick={() => onGo("trips")} />
        <Kpi icon="checkCircle" value={`${t.lab_ready}/${t.lab_open}`} label="приёмок к выдаче" tone={t.lab_ready ? "ok" : ""} onClick={() => onGo("field-work")} />
        <Kpi icon="doc" value={t.review} label="протоколов на нормоконтроле" tone={t.review ? "info" : ""} onClick={() => onGo("journal")} />
        <Kpi icon="alert" value={t.tasks_overdue} label="просроченных задач" tone={t.tasks_overdue ? "bad" : ""} onClick={() => onGo("tasks")} />
      </div>

      <div className="control-split">
        <section>
          <div className="control-head">
            <h2>Сотрудники <span className="count">{data.employees.length}</span></h2>
            <div className="chip-row">
              {([
                ["all", `Все`],
                ["city", `Выезды · ${t.in_city}`],
                ["trip", `Командировки · ${t.on_trip}`],
                ["free", `Свободны · ${t.free}`],
                ["off", "Не в графике"],
              ] as const).map(([key, label]) => (
                <button type="button" key={key} className={`chip ${filter === key ? "active" : ""}`} onClick={() => setFilter(key)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {shown.length === 0 && <EmptyState icon="users" title="Никого">Под этот фильтр сотрудников нет.</EmptyState>}
          <div className="team-grid">
            {shown.map((e) => {
              const color = colors.get(e.id);
              const pct = e.stops.total ? Math.round((e.stops.done / e.stops.total) * 100) : 0;
              return (
                <div className="team-card" key={e.id} style={{ "--vc": color } as CSSProperties}>
                  <div className="team-card-head">
                    <span className="avatar" style={{ background: color }}>{initials(e.full_name)}</span>
                    <div className="grow">
                      <b>{e.full_name}</b>
                      <div className="sub">{e.position || "—"}</div>
                    </div>
                    <span className={`pill ${STATE[e.state].tone}`}>{STATE[e.state].label}</span>
                  </div>

                  {e.trips.map((trip) => (
                    <button type="button" key={trip.id} className="team-line link-line" onClick={() => onGo("trips", trip.id)}>
                      <Icon name="briefcase" size={14} />
                      <span className="grow">{trip.title}{trip.destination ? ` · ${trip.destination}` : ""}</span>
                      {trip.is_lead && <span className="pill plain muted">старший</span>}
                    </button>
                  ))}

                  {e.stops.total > 0 ? (
                    <button type="button" className="team-line link-line" disabled={!e.city_trip_id && !e.trips.length}
                            onClick={() => onGo("trips", e.city_trip_id ?? e.trips[0]?.id)}>
                      <Icon name="car" size={14} />
                      <span className="grow">
                        Адресов: <b>{e.stops.done}</b> из {e.stops.total}
                        {e.stops.missed ? ` · не вышло ${e.stops.missed}` : ""}
                      </span>
                      <Icon name="chevronRight" size={14} />
                    </button>
                  ) : e.slots.length > 0 ? (
                    <div className="team-line">
                      <Icon name="calendar" size={14} />
                      <span className="grow">
                        Может выезжать: {e.slots.map((s) => (s.start ? `${s.start}–${s.end}` : "весь день")).join(", ")}
                      </span>
                    </div>
                  ) : null}
                  {e.stops.total > 0 && <div className="mini-bar"><i style={{ width: `${pct}%`, background: color }} /></div>}

                  <div className="team-metrics">
                    <span title="Приёмки в работе"><Icon name="checkCircle" size={13} /> {e.lab_open}</span>
                    <span title="Черновики протоколов"><Icon name="pen" size={13} /> {e.drafts}</span>
                    <span title="На нормоконтроле" className={e.ready ? "info" : ""}><Icon name="doc" size={13} /> {e.ready}</span>
                    {e.rejected > 0 && <span title="Возвращены на доработку" className="bad"><Icon name="undo" size={13} /> {e.rejected}</span>}
                    <span title="Открытые задачи" className={e.tasks_overdue ? "bad" : ""}>
                      <Icon name="tasks" size={13} /> {e.tasks_open}{e.tasks_overdue ? ` (${e.tasks_overdue} просроч.)` : ""}
                    </span>
                    <span title="Адресов на 7 дней вперёд"><Icon name="calendar" size={13} /> {e.week_stops} на неделе</span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <aside>
          <div className="control-head">
            <h2>Требует внимания <span className="count">{data.attention.length}</span></h2>
          </div>
          {data.attention.length === 0 ? (
            <EmptyState icon="checkCircle" title="Всё разобрано">Новых заявок, выездов без даты и просрочек нет.</EmptyState>
          ) : (
            <div className="card-list attention">
              {data.attention.map((item) => {
                const key = `${item.type}-${item.id}`;
                const assignable = item.type === "request" || item.type === "order";
                return (
                  <div className={`card attention-item ${open === key ? "open" : ""}`} key={key}>
                    <div className="card-head">
                      <Icon name={ATTENTION_ICON[item.type]} size={18} />
                      <div className="grow">
                        <b>{item.title}</b>
                        <div className="sub">
                          {[item.subtitle, item.date && dayLabel(item.date), item.time].filter(Boolean).join(" · ")}
                        </div>
                        {item.suggested_employee && item.type === "request" && (
                          <div className="sub">Подобран: {item.suggested_employee}</div>
                        )}
                      </div>
                      {assignable ? (
                        <button type="button" className={open === key ? "" : "tinted"} onClick={() => setOpen(open === key ? null : key)}>
                          {open === key ? "Скрыть" : "Назначить"}
                        </button>
                      ) : item.type === "issue" ? (
                        <button type="button" onClick={() => onGo("field-work")}>Открыть</button>
                      ) : (
                        <button type="button" onClick={() => onGo("tasks")}>К задачам</button>
                      )}
                    </div>
                    {open === key && (
                      <AssignPicker
                        compact
                        people={people}
                        currentId={item.suggested_employee_id}
                        date={item.date ?? date}
                        withTime
                        busy={busy}
                        onPick={(emp, day, time) => void assign(item, emp, day, time)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {t.unlocated > 0 && (
            <p className="hint" style={{ margin: "10px 4px" }}>
              <Icon name="mapPin" size={13} /> {t.unlocated} адр. без точки на карте — откройте выезд и нажмите «Найти на карте».
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
