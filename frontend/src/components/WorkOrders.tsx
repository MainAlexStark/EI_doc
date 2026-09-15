import { useCallback, useEffect, useState } from "react";
import { fetchWorkOrders, setWorkOrderStatus, type WorkOrder, type WorkOrderFilters } from "../api";

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "—";

function StatusPill({ status }: { status: string }) {
  if (status === "done") return <span className="pill ok">закрыт</span>;
  if (status === "cancelled") return <span className="pill bad">отменён</span>;
  if (status === "in_progress") return <span className="pill warn">в работе</span>;
  return <span className="pill muted">запланирован</span>;
}

export default function WorkOrders() {
  const [filters, setFilters] = useState<WorkOrderFilters>({});
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async (active: WorkOrderFilters) => {
    setLoading(true);
    setError("");
    try {
      setOrders(await fetchWorkOrders(active));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить наряды");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filters);
  }, [filters, load]);

  const update = (patch: Partial<WorkOrderFilters>) => setFilters((current) => ({ ...current, ...patch }));

  const cancel = async (order: WorkOrder) => {
    if (!window.confirm(`Отменить наряд №${order.id}?`)) return;
    setBusy(order.id);
    try {
      await setWorkOrderStatus(order.id, "cancelled");
      await load(filters);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отменить наряд");
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="filters">
        <div className="field">
          <label htmlFor="wo-status">Статус</label>
          <select id="wo-status" value={filters.status ?? ""} onChange={(event) => update({ status: event.target.value })}>
            <option value="">все</option>
            <option value="planned">запланированы</option>
            <option value="in_progress">в работе</option>
            <option value="done">закрыты</option>
            <option value="cancelled">отменены</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="wo-from">С</label>
          <input id="wo-from" type="date" value={filters.date_from ?? ""} onChange={(event) => update({ date_from: event.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="wo-to">по</label>
          <input id="wo-to" type="date" value={filters.date_to ?? ""} onChange={(event) => update({ date_to: event.target.value })} />
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>№</th>
              <th>Дата</th>
              <th>Объект</th>
              <th>Клиент</th>
              <th>Исполнитель</th>
              <th>Поверок</th>
              <th>Состояние</th>
              <th>Заявка</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <tr key={order.id}>
                <td className="num">{order.id}</td>
                <td className="num">{formatDate(order.scheduled_date)}</td>
                <td>{order.site}</td>
                <td>{order.client}</td>
                <td>{order.assigned_employee}</td>
                <td className="num">
                  {order.verifications_accepted} / {order.verifications_total}
                </td>
                <td>
                  <StatusPill status={order.status} />
                </td>
                <td className="num">{order.request_id ? `№${order.request_id}` : "—"}</td>
                <td>
                  {(order.status === "planned" || order.status === "in_progress") && (
                    <button disabled={busy === order.id} onClick={() => void cancel(order)}>
                      Отменить
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {loading && <div className="loading">Загружаю…</div>}
        {!loading && orders.length === 0 && <div className="empty">Под эти фильтры ничего не попало</div>}
      </div>
    </>
  );
}
