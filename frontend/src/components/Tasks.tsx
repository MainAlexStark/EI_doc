import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createTask,
  fetchEmployees,
  fetchTaskBoard,
  fetchTaskCalendar,
  fetchTasks,
  fetchWorkload,
  updateTask,
  type Employee,
  type Me,
  type Task,
  type TaskBoard,
  type Workload,
} from "../api";
import Icon from "../ui/Icon";
import { Modal } from "../ui/Modal";
import { Alert, EmptyState, PageActions, SearchField, Segmented, Spinner } from "../ui/controls";
import { PHONE, useMediaQuery, usePersistent } from "../ui/hooks";
import CalendarView, {
  addDays,
  CalendarToolbar,
  fromIso,
  startOfWeek,
  toIso,
  visibleDays,
  type CalEvent,
  type CalView,
} from "./CalendarView";

type View = "list" | "board" | "calendar" | "workload";

const STATUS_LABELS: Record<string, string> = {
  todo: "к выполнению", in_progress: "в работе", done: "выполнена", cancelled: "отменена",
};
const STATUS_PILL: Record<string, string> = { todo: "muted", in_progress: "info", done: "ok", cancelled: "bad" };
const PRIORITIES = [
  { value: "low", label: "Низкий" },
  { value: "normal", label: "Обычный" },
  { value: "high", label: "Высокий" },
  { value: "urgent", label: "Срочно" },
] as const;
const PRIORITY_LABEL: Record<string, string> = Object.fromEntries(PRIORITIES.map((p) => [p.value, p.label]));
const BOARD_COLUMNS: { key: string; title: string; color: string }[] = [
  { key: "todo", title: "К выполнению", color: "#8e8e93" },
  { key: "in_progress", title: "В работе", color: "var(--accent)" },
  { key: "done", title: "Выполнено", color: "var(--ok-dot)" },
];
const WEEK_HOURS = 40;

const today = () => toIso(new Date());
const dueLabel = (value: string | null) => {
  if (!value) return "";
  const t = today();
  if (value === t) return "сегодня";
  if (value === toIso(addDays(new Date(), 1))) return "завтра";
  if (value === toIso(addDays(new Date(), -1))) return "вчера";
  return fromIso(value).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
};
const isOverdue = (task: Task) => Boolean(task.due_date && task.due_date < today() && task.status !== "done" && task.status !== "cancelled");
const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";

export default function Tasks({ me }: { me: Me | null }) {
  const [view, setView] = usePersistent<View>("ei_doc_tasks_view", "list", ["list", "board", "calendar", "workload"]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [editor, setEditor] = useState<{ task?: Task; due?: string } | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    void fetchEmployees().then(setEmployees).catch(() => undefined);
  }, []);

  const reload = () => setVersion((v) => v + 1);

  return (
    <>
      <PageActions>
        <button type="button" className="primary" onClick={() => setEditor({})}>
          <Icon name="plus" size={16} strokeWidth={2.2} />
          Новая задача
        </button>
      </PageActions>

      <div className="toolbar">
        <Segmented
          value={view}
          onChange={setView}
          ariaLabel="Вид"
          options={[
            { value: "list", label: "Список", icon: "list" },
            { value: "board", label: "Доска", icon: "board" },
            { value: "calendar", label: "Календарь", icon: "calendar" },
            { value: "workload", label: "Загрузка", icon: "chart" },
          ]}
        />
      </div>

      {view === "list" && <TaskList key={version} me={me} onOpen={(task) => setEditor({ task })} onChanged={reload} />}
      {view === "board" && <TaskBoardView key={version} onOpen={(task) => setEditor({ task })} onChanged={reload} />}
      {view === "calendar" && (
        <TaskCalendarView key={version} onOpen={(task) => setEditor({ task })} onCreate={(due) => setEditor({ due })} />
      )}
      {view === "workload" && <WorkloadView key={version} />}

      {editor && (
        <TaskEditor
          task={editor.task}
          due={editor.due}
          employees={employees}
          me={me}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            reload();
          }}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------

function TaskCheck({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const done = task.status === "done";
  return (
    <button
      type="button"
      className={`task-check ${done ? "done" : task.status === "in_progress" ? "progress" : ""}`}
      title={done ? "Вернуть в работу" : "Отметить выполненной"}
      aria-label={done ? "Вернуть в работу" : "Отметить выполненной"}
      disabled={busy || task.status === "cancelled"}
      onClick={(event) => {
        event.stopPropagation();
        setBusy(true);
        void updateTask(task.id, { status: done ? "todo" : "done" })
          .then(onChanged)
          .finally(() => setBusy(false));
      }}
    >
      <Icon name="check" size={12} strokeWidth={3} />
    </button>
  );
}

function TaskList({ me, onOpen, onChanged }: { me: Me | null; onOpen: (task: Task) => void; onChanged: () => void }) {
  const phone = useMediaQuery(PHONE);
  const [scope, setScope] = usePersistent<"mine" | "all">("ei_doc_tasks_scope", "mine", ["mine", "all"]);
  const [state, setState] = useState<"active" | "done" | "all">("active");
  const [query, setQuery] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = await fetchTasks(scope === "mine" ? { mine: "1" } : {});
      setTasks(page.results);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить задачи");
    } finally {
      setLoading(false);
    }
  }, [scope]);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks
      .filter((t) => (state === "all" ? true : state === "done" ? t.status === "done" || t.status === "cancelled" : t.status === "todo" || t.status === "in_progress"))
      .filter((t) => !q || `${t.title} ${t.description} ${t.assignee_name} ${t.linked_label}`.toLowerCase().includes(q))
      .sort((a, b) => (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999"));
  }, [tasks, state, query]);

  const activeCount = tasks.filter((t) => t.status === "todo" || t.status === "in_progress").length;

  return (
    <>
      <div className="toolbar">
        {me?.employee && (
          <Segmented
            size="sm"
            value={scope}
            onChange={setScope}
            options={[
              { value: "mine", label: "Мои" },
              { value: "all", label: "Все" },
            ]}
          />
        )}
        <Segmented
          size="sm"
          value={state}
          onChange={setState}
          options={[
            { value: "active", label: "Активные", count: activeCount },
            { value: "done", label: "Завершённые" },
            { value: "all", label: "Все" },
          ]}
        />
        <SearchField className="grow" value={query} onChange={setQuery} placeholder="Найти задачу" />
      </div>
      {error && <Alert>{error}</Alert>}
      {phone && !loading && shown.length > 0 && (
        <div className="m-list">
          {shown.map((task) => (
            <div role="button" tabIndex={0} key={task.id} className={`m-row ${task.status === "done" ? "done" : ""}`} onClick={() => onOpen(task)} onKeyDown={(e) => e.key === "Enter" && onOpen(task)}>
              <TaskCheck task={task} onChanged={() => { void load(); onChanged(); }} />
              <span className="grow">
                <span className="m-title">{task.title}</span>
                <span className="m-meta">
                  <span className={`prio ${task.priority}`}>{PRIORITY_LABEL[task.priority] ?? task.priority}</span>
                  {task.due_date && <span className={isOverdue(task) ? "due over" : ""}><Icon name="calendar" size={12} /> {dueLabel(task.due_date)}</span>}
                  {task.assignee_name && <span>{task.assignee_name}</span>}
                  {task.status === "in_progress" && <span className="pill info">в работе</span>}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
      {phone && loading && <Spinner />}
      {phone && !loading && shown.length === 0 && (
        <EmptyState icon="tasks" title={state === "active" ? "Активных задач нет" : "Задач нет"} />
      )}
      {!phone && <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Задача</th>
              <th>Исполнитель</th>
              <th>Срок</th>
              <th>Приоритет</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {!loading && shown.map((task) => (
              <tr key={task.id} className={`clickable ${task.status === "done" ? "done" : ""}`} onClick={() => onOpen(task)}>
                <td>
                  <div className="task-title-cell">
                    <TaskCheck task={task} onChanged={() => { void load(); onChanged(); }} />
                    <div className="grow">
                      <div className="cell-main">{task.title}</div>
                      {(task.linked_label || task.children_count > 0) && (
                        <div className="sub">
                          {task.linked_label}
                          {task.children_count > 0 && `${task.linked_label ? " · " : ""}подзадачи: ${task.progress}%`}
                        </div>
                      )}
                    </div>
                  </div>
                </td>
                <td>
                  {task.assignee_name ? (
                    <span className="row"><span className="avatar sm">{initials(task.assignee_name)}</span>{task.assignee_name}</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className={`num ${isOverdue(task) ? "due over" : ""}`}>{dueLabel(task.due_date) || <span className="muted">—</span>}</td>
                <td><span className={`prio ${task.priority}`}>{PRIORITY_LABEL[task.priority] ?? task.priority}</span></td>
                <td><span className={`pill ${STATUS_PILL[task.status] ?? "muted"}`}>{STATUS_LABELS[task.status] ?? task.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <Spinner />}
        {!loading && shown.length === 0 && (
          <EmptyState icon="tasks" title={state === "active" ? "Активных задач нет" : "Задач нет"}>
            {query ? "Под запрос ничего не нашлось" : "Новую задачу можно завести кнопкой справа вверху — или попросить ассистента."}
          </EmptyState>
        )}
      </div>}
    </>
  );
}

// ---------------------------------------------------------------------------

function TaskBoardView({ onOpen, onChanged }: { onOpen: (task: Task) => void; onChanged: () => void }) {
  const [board, setBoard] = useState<TaskBoard>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dragId, setDragId] = useState<number | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await fetchTaskBoard());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить доску");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const move = async (taskId: number, status: string) => {
    const from = Object.entries(board).find(([, list]) => list.some((t) => t.id === taskId));
    if (!from || from[0] === status) return;
    const task = from[1].find((t) => t.id === taskId)!;
    // Сразу на экране, потом на сервере — доска не должна «думать» после каждого переноса.
    setBoard((current) => ({
      ...current,
      [from[0]]: (current[from[0]] ?? []).filter((t) => t.id !== taskId),
      [status]: [{ ...task, status }, ...(current[status] ?? [])],
    }));
    try {
      await updateTask(taskId, { status });
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось перенести задачу");
      void load();
    }
  };

  if (loading) return <Spinner />;

  return (
    <>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      <div className="board">
        {BOARD_COLUMNS.map((column) => {
          const list = board[column.key] ?? [];
          return (
            <div
              className={`board-column ${over === column.key ? "drop" : ""}`}
              key={column.key}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(column.key);
              }}
              onDragLeave={() => setOver((o) => (o === column.key ? null : o))}
              onDrop={(e) => {
                e.preventDefault();
                setOver(null);
                if (dragId != null) void move(dragId, column.key);
              }}
            >
              <h4>
                <span className="dot" style={{ background: column.color }} />
                {column.title}
                <span className="count">{list.length}</span>
              </h4>
              {list.length === 0 && <div className="board-empty">Перетащите задачу сюда</div>}
              {list.map((task) => (
                <button
                  type="button"
                  className={`board-card ${dragId === task.id ? "dragging" : ""}`}
                  key={task.id}
                  draggable
                  onDragStart={(e) => {
                    setDragId(task.id);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragEnd={() => setDragId(null)}
                  onClick={() => onOpen(task)}
                >
                  <div className="title">{task.title}</div>
                  <div className="meta">
                    <span className={`prio ${task.priority}`}>{PRIORITY_LABEL[task.priority]}</span>
                    {task.due_date && (
                      <span className={`due ${isOverdue(task) ? "over" : ""}`}>
                        <Icon name="calendar" size={12} /> {dueLabel(task.due_date)}
                      </span>
                    )}
                    <span className="grow" />
                    {task.assignee_name ? (
                      <span className="avatar sm" title={task.assignee_name}>{initials(task.assignee_name)}</span>
                    ) : (
                      <span className="muted">без исполнителя</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------

function TaskCalendarView({ onOpen, onCreate }: { onOpen: (task: Task) => void; onCreate: (due: string) => void }) {
  const [view, setView] = useState<CalView>("month");
  const [cursor, setCursor] = useState(() => new Date());
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const days = useMemo(() => visibleDays(view, cursor), [view, cursor]);

  useEffect(() => {
    setLoading(true);
    fetchTaskCalendar(days[0], days[days.length - 1])
      .then((calendar) => setTasks(Object.values(calendar).flat()))
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить календарь"))
      .finally(() => setLoading(false));
  }, [days]);

  const events: CalEvent[] = tasks
    .filter((t) => t.due_date)
    .map((t) => ({
      id: String(t.id),
      date: t.due_date!,
      title: t.title,
      subtitle: t.assignee_name || "без исполнителя",
      color: t.status === "done" ? "gray" : t.priority === "urgent" ? "red" : t.priority === "high" ? "orange" : t.status === "in_progress" ? "blue" : "indigo",
      done: t.status === "done",
    }));

  return (
    <div className="card white" style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column", height: "calc(100dvh - var(--head-top, 0px) - 140px)", minHeight: 520 }}>
      <CalendarToolbar view={view} cursor={cursor} onCursor={setCursor} onView={setView} views={["week", "month", "agenda"]} loading={loading} />
      {error && <div style={{ padding: "10px 16px 0" }}><Alert>{error}</Alert></div>}
      <div className="gc-view">
        <CalendarView
          view={view}
          cursor={cursor}
          events={events}
          onCreate={(selection) => onCreate(selection.dates[0])}
          onOpen={(event) => {
            const task = tasks.find((t) => String(t.id) === event.id);
            if (task) onOpen(task);
          }}
          onDay={(date) => {
            setCursor(fromIso(date));
            setView("week");
          }}
          emptyAgenda={<EmptyState icon="calendar" title="Задач со сроком на ближайший месяц нет" />}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function WorkloadView() {
  const [monday, setMonday] = useState(() => startOfWeek(new Date()));
  const [data, setData] = useState<Workload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const from = toIso(monday);
  const to = toIso(addDays(monday, 6));

  useEffect(() => {
    setLoading(true);
    fetchWorkload(from, to)
      .then(setData)
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить загрузку"))
      .finally(() => setLoading(false));
  }, [from, to]);

  const label = `${monday.getDate()} – ${addDays(monday, 6).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}`;
  const isThisWeek = toIso(startOfWeek(new Date())) === from;

  return (
    <>
      <div className="toolbar">
        <div className="btn-group">
          <button type="button" className="icon-btn" aria-label="Предыдущая неделя" onClick={() => setMonday(addDays(monday, -7))}>
            <Icon name="chevronLeft" />
          </button>
          <button type="button" className="icon-btn" aria-label="Следующая неделя" onClick={() => setMonday(addDays(monday, 7))}>
            <Icon name="chevronRight" />
          </button>
        </div>
        <b style={{ fontSize: 15 }}>{label}</b>
        {!isThisWeek && <button type="button" className="sm" onClick={() => setMonday(startOfWeek(new Date()))}>Эта неделя</button>}
        <span className="spacer" />
        <span className="toolbar-note">Сумма оценок задач со сроком на неделе · норма {WEEK_HOURS} ч</span>
      </div>
      {error && <Alert>{error}</Alert>}
      {loading && <Spinner />}
      {!loading && data && data.employees.length === 0 && (
        <EmptyState icon="chart" title="На этой неделе задач с оценкой нет">
          Укажите в задаче срок и оценку в часах — здесь появится загрузка по сотрудникам.
        </EmptyState>
      )}
      {!loading && data && data.employees.length > 0 && (
        <div className="workload">
          {data.employees.map((row) => {
            const pct = (row.hours / WEEK_HOURS) * 100;
            return (
              <div className="workload-row" key={row.employee_id}>
                <div className="who"><span className="avatar sm">{initials(row.employee)}</span><span>{row.employee}</span></div>
                <div className="bar-wrap">
                  <div className={`bar ${pct > 100 ? "over" : pct > 80 ? "high" : ""}`} style={{ width: `${Math.min(100, pct)}%` }} />
                </div>
                <div className="num"><b>{row.hours} ч</b> · {row.count} {plural(row.count, "задача", "задачи", "задач")}</div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};

// ---------------------------------------------------------------------------

function TaskEditor({
  task, due, employees, me, onClose, onSaved,
}: {
  task?: Task;
  due?: string;
  employees: Employee[];
  me: Me | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [assignee, setAssignee] = useState(String(task?.assignee ?? (task ? "" : me?.employee?.id ?? "")));
  const [dueDate, setDueDate] = useState(task?.due_date ?? due ?? "");
  const [priority, setPriority] = useState(task?.priority ?? "normal");
  const [hours, setHours] = useState(task?.estimated_hours ? String(Number(task.estimated_hours)) : "");
  const [status, setStatus] = useState(task?.status ?? "todo");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    if (!title.trim()) {
      setError("Напишите, что нужно сделать");
      return;
    }
    setBusy(true);
    setError("");
    const payload: Partial<Task> = {
      title: title.trim(),
      description,
      assignee: assignee ? Number(assignee) : null,
      due_date: dueDate || null,
      priority,
      estimated_hours: hours ? hours.replace(",", ".") : null,
    };
    try {
      if (task) await updateTask(task.id, { ...payload, status });
      else await createTask(payload);
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить задачу");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={task ? "Задача" : "Новая задача"}
      subtitle={task?.created_by_name ? `Поставил ${task.created_by_name}` : undefined}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Сохраняю…" : task ? "Сохранить" : "Создать"}
          </button>
        </>
      }
    >
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="field">
          <label htmlFor="task-title">Что сделать</label>
          <input id="task-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Например, сверить эталоны с ФГИС" autoFocus />
        </div>
        <div className="field">
          <label htmlFor="task-desc">Подробности <span className="opt">— необязательно</span></label>
          <textarea id="task-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          <div className="field">
            <label htmlFor="task-assignee">Исполнитель</label>
            <select id="task-assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              <option value="">без исполнителя</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>{employee.full_name}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="task-due">Срок</label>
            <input id="task-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="task-hours">Оценка, ч</label>
            <input id="task-hours" inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value.replace(/[^\d.,]/g, ""))} placeholder="например, 2" />
          </div>
        </div>
        <div className="field">
          <span className="field-label">Приоритет</span>
          <Segmented value={priority} onChange={setPriority} options={PRIORITIES.map((p) => ({ value: p.value, label: p.label }))} />
        </div>
        {task && (
          <div className="field">
            <span className="field-label">Статус</span>
            <Segmented
              value={status}
              onChange={setStatus}
              options={[
                { value: "todo", label: "К выполнению" },
                { value: "in_progress", label: "В работе" },
                { value: "done", label: "Выполнена" },
                { value: "cancelled", label: "Отменена" },
              ]}
            />
          </div>
        )}
        {task?.linked_label && <div className="hint">Связана: {task.linked_label}</div>}
        {error && <Alert>{error}</Alert>}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
