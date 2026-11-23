/** «Вернуть с доставкой»: обратный выезд в день поверителя («Город»). Отметка «выполнен»
 *  у этого адреса сама ставит приёмке «выдано» (POST /api/orders/{id}/delivery/). */

import { useState } from "react";
import type { Employee, Me } from "../../api";
import { errorText, isoDay } from "../../components/tripUtil";
import Icon from "../../ui/Icon";
import { Sheet } from "../../ui/Sheet";
import toast from "../../ui/Toast";
import { Alert } from "../../ui/controls";
import type { Go } from "../types";
import { createDelivery, type IntakeOrder } from "./api";

const WINDOWS: [string, string][] = [["08:00", "10:00"], ["10:00", "12:00"], ["12:00", "14:00"], ["14:00", "16:00"], ["16:00", "18:00"]];

export default function DeliverySheet({
  order, me, isManager, employees, go, onClose, onDone,
}: {
  order: IntakeOrder;
  me: Me | null;
  isManager: boolean;
  employees: Employee[];
  go: Go;
  onClose: () => void;
  onDone: () => void;
}) {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const [date, setDate] = useState(isoDay(tomorrow));
  const [window, setWindow] = useState<[string, string] | null>(null);
  const [employee, setEmployee] = useState<number | "">(isManager ? order.assigned_employee_id : me?.employee?.id ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await createDelivery(order.id, {
        date, employee_id: employee ? Number(employee) : null,
        window_start: window?.[0] ?? null, window_end: window?.[1] ?? null, note: note.trim(),
      });
      toast.success(`Доставка: ${result.trip_title}`, {
        description: `${order.client} · ${result.employee}`,
        action: { label: "К выезду", onClick: () => go("city", { action: "open-trip", payload: result.trip_id }) },
      });
      onDone();
    } catch (exc) {
      setError(errorText(exc, "Не удалось завести доставку"));
      setBusy(false);
    }
  };

  return (
    <Sheet
      open
      onClose={onClose}
      size="sm"
      title="Вернуть с доставкой"
      subtitle={`${order.client} · № ${order.id}`}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <span className="spacer" />
          <button type="button" className="primary" disabled={busy || !date} onClick={() => void save()}>
            <Icon name="truck" size={15} /> {busy ? "Завожу…" : "В день поверителя"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <p className="sub">Адрес — откуда забирали приборы (или юридический). Когда поверитель отметит адрес выполненным, приёмка станет «выдано».</p>
        <div className="field">
          <label htmlFor="dl-date">День</label>
          <input id="dl-date" type="date" value={date} min={isoDay(new Date())} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div className="field">
          <span className="field-label">Окно для заказчика</span>
          <div className="chip-row">
            <button type="button" className={`chip ${window == null ? "active" : ""}`} onClick={() => setWindow(null)}>без времени</button>
            {WINDOWS.map((w) => (
              <button type="button" key={w[0]} className={`chip data ${window?.[0] === w[0] ? "active" : ""}`} onClick={() => setWindow(w)}>
                {w[0].slice(0, 2)}–{w[1].slice(0, 2)}
              </button>
            ))}
          </div>
        </div>
        {isManager && employees.length > 0 && (
          <div className="field">
            <label htmlFor="dl-emp">Кто везёт</label>
            <select id="dl-emp" value={employee} onChange={(e) => setEmployee(e.target.value ? Number(e.target.value) : "")}>
              {employees.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="dl-note">Примечание</label>
          <input id="dl-note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="Позвонить за час, проходная № 2" />
        </div>
        {error && <Alert>{error}</Alert>}
      </div>
    </Sheet>
  );
}
