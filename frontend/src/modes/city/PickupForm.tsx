import { useEffect, useRef, useState } from "react";
import {
  fetchEmployees,
  fetchVisitDefaults,
  suggestParties,
  type Employee,
  type Me,
  type PartySuggestion,
  type VisitDefaults,
} from "../../api";
import { WindowPresets, plusHours } from "../../components/TripBits";
import { errorText, isoDay } from "../../components/tripUtil";
import Icon from "../../ui/Icon";
import { Alert } from "../../ui/controls";
import { Sheet } from "../../ui/Sheet";
import { createPickup, type StopWithTrip } from "./api";

/** «Забрать коробку»: предприятие отдаёт приборы на поверку в лабораторию.
 *  Заказчик по названию или ИНН (своя база → DaData), адрес — юридический, если не указать свой. */
export default function PickupForm({
  me, date, onClose, onCreated,
}: {
  me: Me;
  date: string;
  onClose: () => void;
  onCreated: (stop: StopWithTrip) => void;
}) {
  const [defaults, setDefaults] = useState<VisitDefaults>({ work_day: ["08:00", "17:00"], city: "Киров", can_assign: false });
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [query, setQuery] = useState("");
  const [party, setParty] = useState<PartySuggestion | null>(null);
  const [options, setOptions] = useState<PartySuggestion[]>([]);
  const [address, setAddress] = useState("");
  const [contact, setContact] = useState("");
  const [phone, setPhone] = useState("");
  const [count, setCount] = useState("");
  const [day, setDay] = useState(date);
  const [from, setFrom] = useState("");
  const [till, setTill] = useState("");
  const [employeeId, setEmployeeId] = useState<number | "">(me.employee?.id ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const seq = useRef(0);

  useEffect(() => {
    fetchVisitDefaults()
      .then((loaded) => {
        setDefaults(loaded);
        if (loaded.can_assign) fetchEmployees().then(setEmployees).catch(() => undefined);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 3 || (party && party.name === query)) {
      setOptions([]);
      return;
    }
    const run = ++seq.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      suggestParties(q, controller.signal)
        .then((rows) => run === seq.current && setOptions(rows.slice(0, 6)))
        .catch(() => undefined);
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, party]);

  const choose = (row: PartySuggestion) => {
    setParty(row);
    setQuery(row.name);
    setOptions([]);
    if (!address) setAddress(row.address);
    if (!phone && row.phone) setPhone(row.phone);
  };

  const save = async () => {
    const digits = query.replace(/\D/g, "");
    const byInn = !party && /^\d{10}(\d{2})?$/.test(digits) && digits.length === query.trim().length;
    if (!party && !query.trim()) {
      setError("Укажите заказчика — название или ИНН");
      return;
    }
    if (!party && !byInn && !address.trim()) {
      setError("Новый заказчик без ИНН — укажите адрес, откуда забрать");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await createPickup({
        client_id: party?.client_id ?? null,
        inn: party ? party.inn : byInn ? digits : "",
        name: party ? party.name : byInn ? "" : query.trim(),
        address: address.trim(),
        contact_name: contact.trim(),
        contact_phone: phone.trim(),
        planned_count: count ? Number(count) : null,
        date: day,
        window_start: from || null,
        window_end: till || null,
        employee_id: employeeId === "" ? null : Number(employeeId),
        note: note.trim(),
      });
      onCreated(result);
    } catch (exc) {
      setError(errorText(exc, "Не удалось записать забор"));
      setBusy(false);
    }
  };

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);

  return (
    <Sheet
      open
      onClose={onClose}
      title={<><Icon name="box" size={18} /> Забрать коробку</>}
      subtitle="Предприятие отдаёт приборы на поверку в лабораторию"
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Записываю…" : "Записать"}
          </button>
        </>
      }
    >
      <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <div className="field city-suggest">
          <label htmlFor="pickup-client">Заказчик</label>
          <input
            id="pickup-client"
            autoFocus
            value={query}
            autoComplete="off"
            placeholder="Название или ИНН"
            onChange={(event) => { setQuery(event.target.value); setParty(null); }}
          />
          {options.length > 0 && (
            <div className="city-suggest-list" role="listbox">
              {options.map((row) => (
                <button type="button" key={`${row.inn}-${row.kpp}-${row.name}`} role="option" aria-selected={false} onClick={() => choose(row)}>
                  <b>{row.name}</b>
                  <span className="sub"><span className="data">ИНН {row.inn}</span>{row.address ? ` · ${row.address}` : ""}{row.source === "own" ? " · уже работали" : ""}</span>
                </button>
              ))}
            </div>
          )}
          {party && <div className="hint">ИНН <span className="data">{party.inn}</span>{party.source === "own" ? " · есть в базе" : " · из DaData"}</div>}
        </div>
        <div className="field">
          <label htmlFor="pickup-address">Откуда забрать <span className="opt">— пусто: юридический адрес</span></label>
          <input id="pickup-address" value={address} onChange={(event) => setAddress(event.target.value)} placeholder="Город, улица, дом, проходная" />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="pickup-contact">Кто отдаст</label>
            <input id="pickup-contact" value={contact} onChange={(event) => setContact(event.target.value)} placeholder="ФИО, должность" />
          </div>
          <div className="field">
            <label htmlFor="pickup-phone">Телефон</label>
            <input id="pickup-phone" type="tel" inputMode="tel" value={phone} onChange={(event) => setPhone(event.target.value)} />
          </div>
        </div>
        <div className="field-row">
          <div className="field" style={{ flex: "0 0 140px" }}>
            <label htmlFor="pickup-count">Приборов, примерно</label>
            <input id="pickup-count" inputMode="numeric" value={count} onChange={(event) => setCount(event.target.value.replace(/\D/g, ""))} />
          </div>
          <div className="field">
            <label htmlFor="pickup-day">День</label>
            <input id="pickup-day" type="date" value={day} onChange={(event) => setDay(event.target.value)} />
            <div className="chip-row">
              <button type="button" className={`chip ${day === isoDay(new Date()) ? "active" : ""}`} onClick={() => setDay(isoDay(new Date()))}>Сегодня</button>
              <button type="button" className={`chip ${day === isoDay(tomorrow) ? "active" : ""}`} onClick={() => setDay(isoDay(tomorrow))}>Завтра</button>
            </div>
          </div>
        </div>
        <div className="field">
          <span className="field-label">Когда ждут</span>
          <WindowPresets from={from} till={till} workDay={defaults.work_day} onPick={(a, b) => { setFrom(a); setTill(b); }} />
          <div className="field-row">
            <input aria-label="с" type="time" value={from} onChange={(event) => {
              const next = event.target.value;
              if (next && (!till || till === plusHours(from, 2))) setTill(plusHours(next, 2));
              setFrom(next);
            }} />
            <input aria-label="до" type="time" value={till} onChange={(event) => setTill(event.target.value)} />
          </div>
        </div>
        {defaults.can_assign && (
          <div className="field">
            <label htmlFor="pickup-employee">Кто заберёт</label>
            <select id="pickup-employee" value={employeeId} onChange={(event) => setEmployeeId(event.target.value ? Number(event.target.value) : "")}>
              {!me.employee && <option value="">— выберите —</option>}
              {employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="pickup-note">Заметка <span className="opt">— необязательно</span></label>
          <input id="pickup-note" value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} placeholder="Пропуск, склад №2, позвонить за час…" />
        </div>
        {error && <Alert>{error}</Alert>}
      </form>
    </Sheet>
  );
}
