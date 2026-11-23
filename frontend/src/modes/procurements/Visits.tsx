/** Вкладка «Заходы»: командировки по закупке, план/факт и «Спланировать заходы» по перечню СИ. */

import { useEffect, useState } from "react";
import { fetchEmployees, type Employee, type Procurement } from "../../api";
import { TRIP_TONE, dateRange, errorText, instruments, ruDate } from "../../components/tripUtil";
import { EmptyState } from "../../ui/EmptyState";
import Icon from "../../ui/Icon";
import { Sheet } from "../../ui/Sheet";
import { SkeletonText } from "../../ui/Skeleton";
import toast from "../../ui/Toast";
import { Alert } from "../../ui/controls";
import type { Go } from "../types";
import { PlanBar } from "./List";
import { planVisits, type VisitPlan } from "./api";

export default function VisitsTab({
  item, canEdit, go, onChanged,
}: {
  item: Procurement;
  canEdit: boolean;
  go: Go;
  onChanged: () => void;
}) {
  const [planning, setPlanning] = useState(false);
  const closed = item.status === "done" || item.status === "cancelled";
  const { planned, ready, entered, unsuitable } = item.progress;

  return (
    <div className="pr-visits">
      <div className="pr-visits-head card">
        <div className="grow">
          <div className="sub">Поверено за все заходы</div>
          <PlanBar planned={planned} ready={ready} entered={entered} />
          <div className="sub">
            {entered > ready ? `без измерений ещё ${entered - ready}` : ""}
            {unsuitable > 0 ? `${entered > ready ? " · " : ""}непригодных ${unsuitable}` : ""}
          </div>
        </div>
        {canEdit && !closed && (
          <div className="pr-visits-actions">
            <button type="button" className="primary" onClick={() => setPlanning(true)}><Icon name="calendar" size={15} /> Спланировать заходы</button>
            <button type="button" onClick={() => go("trips", { action: "new-trip", payload: item.id })}><Icon name="plus" size={15} /> Один заход</button>
          </div>
        )}
      </div>

      {item.trips.length === 0 ? (
        <EmptyState
          icon="car"
          title="Заходов ещё не было"
          compact
          action={canEdit && !closed ? { label: "Спланировать заходы", icon: "calendar", onClick: () => setPlanning(true) } : undefined}
        >
          {canEdit ? "План разложит перечень СИ из выжимки по адресам и рабочим дням и заведёт командировки." : "Командировок по закупке, где вы участвуете, нет."}
        </EmptyState>
      ) : (
        <div className="pr-trip-list">
          {item.trips.map((trip, index) => (
            <button type="button" key={trip.id} className="card tap pr-trip" onClick={() => go("trips", { action: "open-trip", payload: trip.id })}>
              <div className="pr-trip-n data">{index + 1}</div>
              <div className="grow">
                <b>{trip.title || `Заход ${index + 1}`}</b>
                <div className="sub">{[dateRange(trip.date_start, trip.date_end), trip.lead.full_name, ...trip.members.map((m) => m.full_name)].filter(Boolean).join(" · ")}</div>
              </div>
              <span className={`pill ${TRIP_TONE[trip.status]}`}>{trip.status_display}</span>
              <div className="order-progress">
                {trip.stops_total > 0 ? `${trip.stops_done} из ${trip.stops_total}` : "нет адресов"}
                {trip.instruments_entered > 0 && <small>{instruments(trip.instruments_entered)}</small>}
              </div>
            </button>
          ))}
        </div>
      )}

      {planning && (
        <PlanSheet
          item={item}
          onClose={() => setPlanning(false)}
          onApplied={(plan) => {
            setPlanning(false);
            onChanged();
            const created = plan.created ?? [];
            toast.success(`Заведено командировок: ${created.length}`, {
              action: created[0] ? { label: "Открыть первую", onClick: () => go("trips", { action: "open-trip", payload: created[0].id }) } : undefined,
            });
          }}
        />
      )}
    </div>
  );
}

function PlanSheet({ item, onClose, onApplied }: { item: Procurement; onClose: () => void; onApplied: (plan: VisitPlan) => void }) {
  const [perDay, setPerDay] = useState(40);
  const [days, setDays] = useState(5);
  const [start, setStart] = useState("");
  const [lead, setLead] = useState<number | "">("");
  const [members, setMembers] = useState<number[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [plan, setPlan] = useState<VisitPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchEmployees().then(setEmployees).catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setError("");
      planVisits(item.id, { per_day: perDay || 1, days_per_trip: days || 1, start: start || null })
        .then(setPlan)
        .catch((exc) => {
          setPlan(null);
          setError(errorText(exc, "Не удалось построить план"));
        });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [item.id, perDay, days, start]);

  const apply = async () => {
    setBusy(true);
    try {
      const result = await planVisits(item.id, {
        per_day: perDay || 1, days_per_trip: days || 1, start: start || null, apply: true,
        lead_id: lead ? Number(lead) : null, member_ids: members,
      });
      onApplied(result);
    } catch (exc) {
      setError(errorText(exc, "Не удалось завести командировки"));
      setBusy(false);
    }
  };

  return (
    <Sheet
      open
      onClose={onClose}
      size="lg"
      title="План заходов"
      subtitle={`${item.label} · ${item.customer}`}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <span className="spacer" />
          <button type="button" className="primary" disabled={!plan || busy || plan.trips.length === 0} onClick={() => void apply()}>
            {busy ? "Завожу…" : plan ? `Применить — ${plan.trips.length} ${plan.trips.length === 1 ? "заход" : "захода(ов)"}` : "Применить"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <div className="field-row">
          <div className="field">
            <label htmlFor="vp-per-day">Приборов в день</label>
            <input id="vp-per-day" className="data" inputMode="numeric" value={perDay} onChange={(e) => setPerDay(Number(e.target.value.replace(/\D/g, "")) || 0)} />
          </div>
          <div className="field">
            <label htmlFor="vp-days">Рабочих дней в заходе</label>
            <input id="vp-days" className="data" inputMode="numeric" value={days} onChange={(e) => setDays(Number(e.target.value.replace(/\D/g, "")) || 0)} />
          </div>
          <div className="field">
            <label htmlFor="vp-start">Начать с</label>
            <input id="vp-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="vp-lead">Старший <span className="opt">— по умолчанию вы</span></label>
            <select id="vp-lead" value={lead} onChange={(e) => setLead(e.target.value ? Number(e.target.value) : "")}>
              <option value="">— я —</option>
              {employees.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
            </select>
          </div>
          <div className="field">
            <span className="field-label">Едут ещё</span>
            <div className="chip-row">
              {employees.filter((e) => e.id !== lead).map((e) => (
                <button type="button" key={e.id} className={`chip ${members.includes(e.id) ? "active" : ""}`}
                        onClick={() => setMembers((cur) => (cur.includes(e.id) ? cur.filter((x) => x !== e.id) : [...cur, e.id]))}>
                  {e.full_name.split(" ")[0]}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {error && <Alert>{error}</Alert>}
      {!error && !plan && <SkeletonText lines={6} />}
      {plan && (
        <div className="pr-plan-preview">
          <p className="sub">
            {plan.instruments_total} шт. по {plan.addresses} адресам · источник — {plan.source === "summary" ? "перечень СИ из выжимки" : "объём по контракту (адресов нет)"}
          </p>
          {plan.warnings.map((w) => <Alert key={w} tone="warn">{w}</Alert>)}
          {plan.trips.map((trip) => (
            <details key={trip.number} className="pr-plan-trip card" open={plan.trips.length <= 2}>
              <summary>
                <b>Заход {trip.number}</b> <span className="sub">{dateRange(trip.date_start, trip.date_end)} · {trip.quantity} шт. · {trip.days.length} дн.</span>
              </summary>
              {trip.days.map((day) => (
                <div key={day.date} className="pr-plan-day">
                  <div className="pr-plan-date data">{ruDate(day.date)} <small>{day.quantity} шт.</small></div>
                  <ul>
                    {day.stops.map((stop, i) => (
                      <li key={i}>
                        <span className="data">{stop.quantity}</span> — {stop.address}
                        {stop.instruments.length > 0 && <div className="sub">{stop.instruments.slice(0, 4).join("; ")}{stop.instruments.length > 4 ? "…" : ""}</div>}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </details>
          ))}
        </div>
      )}
    </Sheet>
  );
}
