import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { downloadOrderAct, fetchEmployees, issueOrder, type Employee, type WorkOrder } from "../../api";
import { assignWork, teamColors } from "../../dispatchApi";
import Icon from "../../ui/Icon";
import { confirmDialog } from "../../ui/Modal";
import { Alert, EmptyState, SearchField, Segmented } from "../../ui/controls";
import { orderStage } from "../Orders";

/** «Приёмки» глазами руководства: все заказы фирмы одной таблицей.
 *
 * Ответственный меняется прямо в строке, несколько заказов — галочками и
 * одним выбором внизу; «Выдано» и акты — без захода в заказ. */

type Stage = "all" | "Приём" | "Поверка" | "Готов к выдаче";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "") : "—");

export default function LabBoard({
  orders, onOpen, onChanged,
}: {
  orders: WorkOrder[];
  onOpen: (id: number) => void;
  onChanged: (order: WorkOrder | null) => void;
}) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [stage, setStage] = useState<Stage>("all");
  const [who, setWho] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    fetchEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, []);
  const colors = useMemo(() => teamColors(employees.map((e) => e.id)), [employees]);

  const lab = orders.filter((o) => o.kind === "lab");
  const q = query.trim().toLowerCase();
  const rows = lab
    .filter((o) => stage === "all" || orderStage(o).label === stage)
    .filter((o) => who == null || o.assigned_employee_id === who)
    .filter((o) => !q || `${o.client} ${o.client_inn} ${o.procurement_label} ${o.id}`.toLowerCase().includes(q))
    .sort((a, b) => (b.received_at ?? b.created_at).localeCompare(a.received_at ?? a.created_at));

  const count = (s: Stage) => lab.filter((o) => orderStage(o).label === s).length;
  const load = (id: number) => lab.filter((o) => o.assigned_employee_id === id).length;

  const assign = async (ids: number[], employeeId: number) => {
    setBusy("assign");
    setError("");
    try {
      const result = await assignWork({ items: ids.map((id) => ({ type: "order", id })), employee_id: employeeId });
      if (result.errors.length) setError(result.errors.map((e) => e.error).join("; "));
      if (result.done.length > 1) setNotice(`Назначено ${result.done.length}`);
      setChecked(new Set());
      onChanged(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось назначить");
    } finally {
      setBusy("");
    }
  };

  const issue = async (o: WorkOrder) => {
    if (o.verifications_ready < o.verifications_total) {
      const ok = await confirmDialog({ title: "Выдать заказ?", text: `Поверено ${o.verifications_ready} из ${o.verifications_total}.`, ok: "Выдать" });
      if (!ok) return;
    }
    setBusy(`issue-${o.id}`);
    try {
      onChanged(await issueOrder(o.id, true));
      setNotice(`${o.client}: выдано`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отметить выдачу");
    } finally {
      setBusy("");
    }
  };

  const act = async (o: WorkOrder, kind: "receive" | "issue") => {
    setBusy(`act-${o.id}`);
    try {
      await downloadOrderAct(o.id, kind);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сформировать акт");
    } finally {
      setBusy("");
    }
  };

  const toggle = (id: number) =>
    setChecked((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allChecked = rows.length > 0 && rows.every((o) => checked.has(o.id));

  return (
    <div className="lab-board">
      {notice && <Alert tone="ok" onClose={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      <div className="toolbar">
        <Segmented
          value={stage}
          onChange={setStage}
          options={[
            { value: "all", label: "Все", count: lab.length },
            { value: "Приём", label: "Приём", count: count("Приём") },
            { value: "Поверка", label: "Поверка", count: count("Поверка") },
            { value: "Готов к выдаче", label: "К выдаче", count: count("Готов к выдаче") },
          ]}
        />
        <SearchField value={query} onChange={setQuery} placeholder="Заказчик, ИНН, закупка" className="grow" />
      </div>
      <div className="chip-row journals">
        <button type="button" className={`chip ${who == null ? "active" : ""}`} onClick={() => setWho(null)}>Все сотрудники</button>
        {employees.filter((e) => load(e.id) > 0).map((e) => (
          <button type="button" key={e.id} className={`chip ${who === e.id ? "active" : ""}`}
                  style={who === e.id ? ({ "--vc": colors.get(e.id), background: colors.get(e.id) } as CSSProperties) : undefined}
                  onClick={() => setWho(who === e.id ? null : e.id)}>
            <span className="v-dot" style={{ background: colors.get(e.id) }} /> {e.full_name.split(" ")[0]} <span className="chip-count">{load(e.id)}</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <EmptyState icon="inbox" title="Заказов нет">Под этот фильтр приёмок нет.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="data-table lab-table">
            <thead>
              <tr>
                <th className="check-col">
                  <input type="checkbox" aria-label="Выбрать все" checked={allChecked}
                         onChange={() => setChecked(allChecked ? new Set() : new Set(rows.map((o) => o.id)))} />
                </th>
                <th>Заказ</th>
                <th>Принят</th>
                <th>Приборы</th>
                <th>Стадия</th>
                <th>Ответственный</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => {
                const st = orderStage(o);
                return (
                  <tr key={o.id} className={checked.has(o.id) ? "checked" : ""}>
                    <td className="check-col"><input type="checkbox" checked={checked.has(o.id)} onChange={() => toggle(o.id)} aria-label={`Выбрать заказ ${o.id}`} /></td>
                    <td>
                      <button type="button" className="link strong" onClick={() => onOpen(o.id)}>{o.client}</button>
                      <div className="sub">№ {o.id}{o.client_inn ? ` · ИНН ${o.client_inn}` : ""}{o.procurement_label ? ` · ${o.procurement_label.split(" · ")[0]}` : ""}</div>
                    </td>
                    <td>{when(o.received_at ?? o.created_at)}</td>
                    <td>
                      {o.verifications_total ? (
                        <div className="order-progress">
                          {o.verifications_ready} из {o.verifications_total}
                          <div className="mini-bar"><i style={{ width: `${(o.verifications_ready / o.verifications_total) * 100}%` }} /></div>
                        </div>
                      ) : <span className="sub">не внесены</span>}
                    </td>
                    <td><span className={`pill ${st.tone}`}>{st.label}</span></td>
                    <td>
                      <div className="emp-select" style={{ "--vc": colors.get(o.assigned_employee_id) } as CSSProperties}>
                        <span className="v-dot" />
                        <select value={o.assigned_employee_id} disabled={!!busy}
                                onChange={(e) => void assign([o.id], Number(e.target.value))} aria-label="Ответственный">
                          {!employees.some((e) => e.id === o.assigned_employee_id) && <option value={o.assigned_employee_id}>{o.assigned_employee}</option>}
                          {employees.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
                        </select>
                      </div>
                    </td>
                    <td className="row-actions">
                      {st.label === "Готов к выдаче" && (
                        <button type="button" className="sm primary" disabled={!!busy} onClick={() => void issue(o)}>Выдано</button>
                      )}
                      {o.verifications_total > 0 && (
                        <button type="button" className="sm" disabled={!!busy} onClick={() => void act(o, st.label === "Готов к выдаче" ? "issue" : "receive")}>
                          <Icon name="doc" size={13} /> {st.label === "Готов к выдаче" ? "Акт выдачи" : "Акт приёма"}
                        </button>
                      )}
                      <button type="button" className="sm" onClick={() => onOpen(o.id)}>Открыть</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {checked.size > 0 && (
        <div className="bulk-bar glass">
          <b>Выбрано: {checked.size}</b>
          <span>Отдать:</span>
          <div className="bulk-people">
            {employees.map((e) => (
              <button type="button" key={e.id} className="assign-person" disabled={!!busy} onClick={() => void assign([...checked], e.id)}>
                <span className="dot" style={{ background: colors.get(e.id) }} /> <span className="who">{e.full_name.split(" ")[0]}</span>
              </button>
            ))}
          </div>
          <button type="button" className="ghost" onClick={() => setChecked(new Set())}>Отмена</button>
        </div>
      )}
    </div>
  );
}
