import { useCallback, useEffect, useState } from "react";
import {
  createTask,
  fetchEmployees,
  fetchTaskBoard,
  fetchTaskCalendar,
  fetchTasks,
  fetchWorkload,
  updateTask,
  type Employee,
  type Task,
  type TaskBoard,
  type TaskCalendar,
  type Workload,
} from "../api";

type View = "mine" | "board" | "calendar" | "workload";

const STATUS_LABELS: Record<string, string> = {
  todo: "к выполнению", in_progress: "в работе", done: "выполнена", cancelled: "отменена",
};
const BOARD_COLUMNS: { key: string; title: string }[] = [
  { key: "todo", title: "К выполнению" },
  { key: "in_progress", title: "В работе" },
  { key: "done", title: "Выполнено" },
];

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "—";

const weekRange = (): [string, string] => {
  const today = new Date();
  const day = (today.getDay() + 6) % 7; // понедельник = 0
  const monday = new Date(today);
  monday.setDate(today.getDate() - day);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return [iso(monday), iso(sunday)];
};

function NewTaskForm({ employees, onCreated }: { employees: Employee[]; onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [hours, setHours] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError("");
    try {
      await createTask({
        title,
        assignee: assignee ? Number(assignee) : null,
        due_date: dueDate || null,
        estimated_hours: hours || null,
      });
      setTitle("");
      setDueDate("");
      setHours("");
      onCreated();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось создать задачу");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="filters" onSubmit={submit}>
      <div className="field">
        <label htmlFor="task-title">Новая задача</label>
        <input id="task-title" style={{ width: 260 }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Что сделать" />
      </div>
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
        <input id="task-hours" style={{ width: 70 }} value={hours} onChange={(e) => setHours(e.target.value)} placeholder="2" />
      </div>
      <button className="primary" type="submit" disabled={busy}>Добавить</button>
      {error && <span className="error" style={{ marginBottom: 0 }}>{error}</span>}
    </form>
  );
}

export default function Tasks() {
  const [view, setView] = useState<View>("mine");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [mine, setMine] = useState<Task[]>([]);
  const [board, setBoard] = useState<TaskBoard>({});
  const [calendar, setCalendar] = useState<TaskCalendar>({});
  const [workload, setWorkload] = useState<Workload | null>(null);
  const [range, setRange] = useState<[string, string]>(weekRange());
  const [showAllMine, setShowAllMine] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    void fetchEmployees().then(setEmployees).catch(() => undefined);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (view === "mine") {
        const page = await fetchTasks(showAllMine ? {} : { mine: "1" });
        setMine(page.results);
      } else if (view === "board") {
        setBoard(await fetchTaskBoard());
      } else if (view === "calendar") {
        setCalendar(await fetchTaskCalendar(range[0], range[1]));
      } else {
        setWorkload(await fetchWorkload(range[0], range[1]));
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить задачи");
    } finally {
      setLoading(false);
    }
  }, [view, range, showAllMine]);

  useEffect(() => {
    void load();
  }, [load]);

  const advance = async (task: Task) => {
    const next: Record<string, string> = { todo: "in_progress", in_progress: "done" };
    const status = next[task.status];
    if (!status) return;
    await updateTask(task.id, { status });
    await load();
  };

  return (
    <>
      <div className="topbar" style={{ padding: "0 0 12px", border: "none", background: "none" }}>
        <nav>
          <button className={view === "mine" ? "active" : ""} onClick={() => setView("mine")}>Мои задачи</button>
          <button className={view === "board" ? "active" : ""} onClick={() => setView("board")}>Доска</button>
          <button className={view === "calendar" ? "active" : ""} onClick={() => setView("calendar")}>Календарь</button>
          <button className={view === "workload" ? "active" : ""} onClick={() => setView("workload")}>Загрузка на неделю</button>
        </nav>
      </div>

      <NewTaskForm employees={employees} onCreated={() => void load()} />

      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Загружаю…</div>}

      {!loading && view === "mine" && (
        <>
          <label style={{ display: "block", marginBottom: 10, color: "var(--muted)", fontSize: 13 }}>
            <input type="checkbox" checked={showAllMine} onChange={(e) => setShowAllMine(e.target.checked)} /> показывать все задачи, не только мои
          </label>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Задача</th>
                  <th>Исполнитель</th>
                  <th>Срок</th>
                  <th>Приоритет</th>
                  <th>Прогресс</th>
                  <th>Статус</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {mine.map((task) => (
                  <tr key={task.id}>
                    <td>
                      {task.title}
                      {task.linked_label && <div className="sub">{task.linked_label}</div>}
                    </td>
                    <td>{task.assignee_name || "—"}</td>
                    <td className="num">{formatDate(task.due_date)}</td>
                    <td>{task.priority}</td>
                    <td className="num">{task.children_count > 0 ? `${task.progress}%` : "—"}</td>
                    <td>
                      <span className={`pill ${task.status === "done" ? "ok" : task.status === "cancelled" ? "bad" : "muted"}`}>
                        {STATUS_LABELS[task.status] ?? task.status}
                      </span>
                    </td>
                    <td>
                      {(task.status === "todo" || task.status === "in_progress") && (
                        <button onClick={() => void advance(task)}>
                          {task.status === "todo" ? "В работу" : "Выполнено"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {mine.length === 0 && <div className="empty">Задач нет</div>}
          </div>
        </>
      )}

      {!loading && view === "board" && (
        <div className="board">
          {BOARD_COLUMNS.map((column) => (
            <div className="board-column" key={column.key}>
              <h4>{column.title} <span className="sub">{(board[column.key] ?? []).length}</span></h4>
              {(board[column.key] ?? []).map((task) => (
                <div className="board-card" key={task.id}>
                  <div className="title">{task.title}</div>
                  <div className="sub">{task.assignee_name || "без исполнителя"}</div>
                  {task.due_date && <div className="sub">до {formatDate(task.due_date)}</div>}
                  {column.key !== "done" && (
                    <button onClick={() => void advance(task)}>
                      {column.key === "todo" ? "В работу →" : "Выполнено →"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {!loading && view === "calendar" && (
        <>
          <div className="filters">
            <div className="field">
              <label htmlFor="cal-from">С</label>
              <input id="cal-from" type="date" value={range[0]} onChange={(e) => setRange([e.target.value, range[1]])} />
            </div>
            <div className="field">
              <label htmlFor="cal-to">по</label>
              <input id="cal-to" type="date" value={range[1]} onChange={(e) => setRange([range[0], e.target.value])} />
            </div>
          </div>
          {Object.keys(calendar).length === 0 && <div className="empty">Задач со сроком в этом диапазоне нет</div>}
          {Object.entries(calendar).sort(([a], [b]) => a.localeCompare(b)).map(([day, tasks]) => (
            <div className="calendar-day" key={day}>
              <h4>{formatDate(day)}</h4>
              <ul>
                {tasks.map((task) => (
                  <li key={task.id}>
                    {task.title} — <span className="sub">{task.assignee_name || "без исполнителя"}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}

      {!loading && view === "workload" && workload && (
        <>
          <div className="filters">
            <div className="field">
              <label htmlFor="wl-from">С</label>
              <input id="wl-from" type="date" value={range[0]} onChange={(e) => setRange([e.target.value, range[1]])} />
            </div>
            <div className="field">
              <label htmlFor="wl-to">по</label>
              <input id="wl-to" type="date" value={range[1]} onChange={(e) => setRange([range[0], e.target.value])} />
            </div>
          </div>
          {workload.employees.length === 0 && <div className="empty">Задач с оценкой в этом диапазоне нет</div>}
          <div className="workload">
            {workload.employees.map((row) => (
              <div className="workload-row" key={row.employee_id}>
                <div className="who">{row.employee}</div>
                <div className="bar-wrap">
                  <div className="bar" style={{ width: `${Math.min(100, (row.hours / 40) * 100)}%` }} />
                </div>
                <div className="num">{row.hours} ч · {row.count} задач</div>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
