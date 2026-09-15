import { useCallback, useEffect, useState } from "react";
import {
  confirmRequest,
  fetchEmployees,
  fetchRequests,
  rejectRequest,
  type Employee,
  type HubRequest,
} from "../api";

const money = (value: string | number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(Number(value));

const STATUS_LABELS: Record<string, string> = {
  new: "новая", routed: "подобран исполнитель", confirmed: "подтверждена",
  rejected: "отклонена", spam: "спам / дубль",
};

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "—";

/** Диспетчерская очередь заявок с сайта: рекомендация -> подтверждение -> наряд. */
export default function RequestsInbox() {
  const [status, setStatus] = useState("");
  const [requests, setRequests] = useState<HubRequest[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [pickEmployee, setPickEmployee] = useState<Record<number, string>>({});
  const [pickDate, setPickDate] = useState<Record<number, string>>({});
  const [pickTime, setPickTime] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (currentStatus: string) => {
    setLoading(true);
    setError("");
    try {
      const [rows, staff] = await Promise.all([
        fetchRequests(currentStatus ? { status: currentStatus } : {}),
        employees.length ? Promise.resolve(employees) : fetchEmployees(),
      ]);
      setRequests(rows);
      setEmployees(staff);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить заявки");
    } finally {
      setLoading(false);
    }
    // employees намеренно не в зависимостях — грузим справочник один раз
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load(status);
  }, [status, load]);

  const startConfirm = (request: HubRequest) => {
    setOpen(request.id);
    setPickEmployee((current) => ({
      ...current,
      [request.id]: current[request.id] ?? (request.suggested_employee_id?.toString() ?? ""),
    }));
    setPickDate((current) => ({ ...current, [request.id]: current[request.id] ?? (request.desired_date ?? "") }));
    setPickTime((current) => ({ ...current, [request.id]: current[request.id] ?? (request.desired_time ?? "") }));
  };

  const confirm = async (request: HubRequest) => {
    const employeeId = pickEmployee[request.id];
    if (!employeeId) {
      setError("Выберите исполнителя");
      return;
    }
    setBusy(request.id);
    setError("");
    try {
      await confirmRequest(request.id, {
        employee_id: Number(employeeId),
        scheduled_date: pickDate[request.id] || null,
        scheduled_time: pickTime[request.id] || null,
      });
      setOpen(null);
      await load(status);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось подтвердить заявку");
    } finally {
      setBusy(null);
    }
  };

  const reject = async (request: HubRequest, spam: boolean) => {
    const reason = spam ? "" : window.prompt("Причина отклонения") ?? "";
    setBusy(request.id);
    setError("");
    try {
      await rejectRequest(request.id, { reason, spam });
      await load(status);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отклонить заявку");
    } finally {
      setBusy(null);
    }
  };

  if (loading && requests.length === 0) return <div className="loading">Загружаю…</div>;

  return (
    <>
      <div className="filters">
        <div className="field">
          <label htmlFor="req-status">Статус</label>
          <select id="req-status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">все, кроме спама</option>
            <option value="new">новые</option>
            <option value="routed">подобран исполнитель</option>
            <option value="confirmed">подтверждены</option>
            <option value="rejected">отклонены</option>
            <option value="spam">спам / дубли</option>
          </select>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {requests.length === 0 && <div className="empty">Заявок под этот фильтр нет</div>}

      <div className="scopes">
        {requests.map((request) => (
          <div className="scope" key={request.id}>
            <h3>№{request.id} — {request.contact_name}</h3>
            <div className="who">
              {request.address}
              {request.district ? ` · ${request.district}` : ""}
            </div>

            <div className="counts">
              <span className="pill muted">{STATUS_LABELS[request.status] ?? request.status}</span>
              {request.suggested_employee && (
                <span className="pill ok">рекомендован: {request.suggested_employee}</span>
              )}
              {request.assigned_employee && (
                <span className="pill ok">исполнитель: {request.assigned_employee}</span>
              )}
              {request.desired_date && (
                <span className="pill muted">
                  желаемая дата: {formatDate(request.desired_date)}
                  {request.desired_time ? `, ${request.desired_time.slice(0, 5)}` : ""}
                </span>
              )}
              {request.is_priority_slot && <span className="pill ok">приоритетный слот</span>}
              {request.estimated_price && (
                <span className="pill muted">≈ {money(request.estimated_price)} ₽</span>
              )}
            </div>

            {request.items.length > 0 && (
              <ul className="request-items">
                {request.items.map((item) => (
                  <li key={item.id}>
                    {item.family} × {item.quantity} — {money(item.subtotal)} ₽
                  </li>
                ))}
              </ul>
            )}
            {request.si_description && <p>{request.si_description}</p>}
            {request.comment && <p className="sub">{request.comment}</p>}
            <p className="sub">
              {request.contact_phone}
              {request.contact_phone && request.contact_email ? " · " : ""}
              {request.contact_email}
            </p>

            {(request.status === "new" || request.status === "routed") && (
              <div className="actions">
                <button disabled={busy === request.id} onClick={() => startConfirm(request)}>
                  Подтвердить
                </button>
                <button disabled={busy === request.id} onClick={() => void reject(request, false)}>
                  Отклонить
                </button>
                <button disabled={busy === request.id} onClick={() => void reject(request, true)}>
                  Спам
                </button>
              </div>
            )}

            {open === request.id && (
              <div className="preview">
                <h4>Подтверждение — создаст наряд</h4>
                <div className="field-row">
                  <div className="field">
                    <label htmlFor={`emp-${request.id}`}>Исполнитель</label>
                    <select
                      id={`emp-${request.id}`}
                      value={pickEmployee[request.id] ?? ""}
                      onChange={(event) =>
                        setPickEmployee((current) => ({ ...current, [request.id]: event.target.value }))
                      }
                    >
                      <option value="">выберите</option>
                      {employees.map((employee) => (
                        <option key={employee.id} value={employee.id}>
                          {employee.full_name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor={`date-${request.id}`}>Дата выезда</label>
                    <input
                      id={`date-${request.id}`}
                      type="date"
                      value={pickDate[request.id] ?? ""}
                      onChange={(event) =>
                        setPickDate((current) => ({ ...current, [request.id]: event.target.value }))
                      }
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`time-${request.id}`}>Время</label>
                    <input
                      id={`time-${request.id}`}
                      type="time"
                      value={pickTime[request.id] ?? ""}
                      onChange={(event) =>
                        setPickTime((current) => ({ ...current, [request.id]: event.target.value }))
                      }
                    />
                  </div>
                </div>
                <div className="actions">
                  <button className="primary" disabled={busy === request.id} onClick={() => void confirm(request)}>
                    Создать наряд
                  </button>
                  <button disabled={busy === request.id} onClick={() => setOpen(null)}>
                    Отмена
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
