import { useEffect, useRef, useState } from "react";
import {
  createVisit,
  fetchEmployees,
  fetchVisitDefaults,
  suggestAddress,
  type AddressSuggestion,
  type Employee,
  type Me,
  type VisitDefaults,
  type VisitResult,
} from "../api";
import { Modal } from "../ui/Modal";
import { Alert, Segmented } from "../ui/controls";
import { WindowPresets, plusHours } from "./TripBits";
import { errorText, isoDay } from "./tripUtil";

/** Запись на поверку прямо в сервисе — когда клиент позвонил или пришёл, а не
 * заполнил форму на сайте. Заводится заявка «по телефону», сразу подтверждается
 * и встаёт в день исполнителя во «Выездах» с окном «ждут с … до …». */
export default function VisitModal({
  me, onClose, onCreated,
}: {
  me: Me;
  onClose: () => void;
  onCreated: (result: VisitResult) => void;
}) {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const [defaults, setDefaults] = useState<VisitDefaults>({ work_day: ["08:00", "17:00"], city: "Киров", can_assign: false });
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [picked, setPicked] = useState<AddressSuggestion | null>(null);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [showList, setShowList] = useState(false);
  const [date, setDate] = useState(isoDay(tomorrow));
  const [from, setFrom] = useState("08:00");
  const [till, setTill] = useState("10:00");
  const [employeeId, setEmployeeId] = useState<number | "">(me.employee?.id ?? "");
  const [quantity, setQuantity] = useState("1");
  const [comment, setComment] = useState("");
  const [source, setSource] = useState<"phone" | "manual">("phone");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = useRef(0);

  useEffect(() => {
    fetchVisitDefaults()
      .then((loaded) => {
        setDefaults(loaded);
        if (loaded.can_assign) {
          setEmployeeId("");  // руководитель: по умолчанию — подбор по району
          fetchEmployees().then(setEmployees).catch(() => undefined);
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const query = address.trim();
    if (query.length < 4 || picked?.value === address) {
      setSuggestions([]);
      return;
    }
    const current = ++run.current;
    const timer = window.setTimeout(() => {
      const city = defaults.city.toLowerCase();
      void suggestAddress(query.toLowerCase().includes(city.slice(0, 4)) ? query : `${defaults.city} ${query}`)
        .then((response) => {
          if (current !== run.current) return;
          setSuggestions(response.results);
          setShowList(response.results.length > 0);
        })
        .catch(() => undefined);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [address, picked, defaults.city]);

  const pick = (item: AddressSuggestion) => {
    setPicked(item);
    setAddress(item.value);
    setShowList(false);
  };

  const save = async () => {
    if (!address.trim()) {
      setError("Укажите адрес");
      return;
    }
    if (!date) {
      setError("Укажите день");
      return;
    }
    setBusy(true);
    setError("");
    const chosen = picked && picked.value === address ? picked : null;
    try {
      onCreated(
        await createVisit({
          contact_name: name.trim(),
          contact_phone: phone.trim(),
          address: address.trim(),
          ...(chosen
            ? {
                postal_code: chosen.postal_code, fias_id: chosen.fias_id, district: chosen.district,
                latitude: chosen.latitude, longitude: chosen.longitude,
              }
            : {}),
          date,
          window_start: from || null,
          window_end: from ? till || null : null,
          employee_id: employeeId ? Number(employeeId) : null,
          quantity: quantity ? Number(quantity) : null,
          comment: comment.trim(),
          source,
        }),
      );
    } catch (exc) {
      setError(errorText(exc, "Не удалось записать"));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Запись на поверку"
      subtitle="Клиент позвонил или пришёл — адрес встанет в день исполнителя"
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Записываю…" : "Записать"}</button>
        </>
      }
    >
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="field-row">
          <div className="field">
            <label htmlFor="visit-name">ФИО <span className="opt">— необязательно</span></label>
            <input id="visit-name" value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" autoFocus />
          </div>
          <div className="field">
            <label htmlFor="visit-phone">Телефон</label>
            <input id="visit-phone" type="tel" inputMode="tel" value={phone} onChange={(event) => setPhone(event.target.value)} autoComplete="off" />
          </div>
        </div>
        <div className="field suggest-field">
          <label htmlFor="visit-address">Адрес</label>
          <input
            id="visit-address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            onFocus={() => setShowList(suggestions.length > 0)}
            onBlur={() => window.setTimeout(() => setShowList(false), 150)}
            placeholder="Улица, дом, квартира"
            autoComplete="off"
          />
          {showList && (
            <ul className="suggestions">
              {suggestions.map((item) => (
                <li key={item.value} onMouseDown={() => pick(item)}>
                  <span>{item.value}</span>
                  {item.postal_code && <span className="sub">{item.postal_code}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="visit-date">День</label>
            <input id="visit-date" type="date" value={date} onChange={(event) => setDate(event.target.value)} />
          </div>
          <div className="field" style={{ flex: "0 0 120px" }}>
            <label htmlFor="visit-quantity">Приборов</label>
            <input id="visit-quantity" inputMode="numeric" value={quantity} onChange={(event) => setQuantity(event.target.value.replace(/\D/g, ""))} />
          </div>
        </div>
        <div className="field">
          <span className="field-label">Когда ждут</span>
          <WindowPresets
            from={from}
            till={till}
            workDay={defaults.work_day}
            onPick={(a, b) => {
              setFrom(a);
              setTill(b);
            }}
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="visit-from">с</label>
            <input
              id="visit-from"
              type="time"
              value={from}
              onChange={(event) => {
                const next = event.target.value;
                if (next && (!till || till === plusHours(from, 2))) setTill(plusHours(next, 2));
                setFrom(next);
              }}
            />
          </div>
          <div className="field">
            <label htmlFor="visit-till">до</label>
            <input id="visit-till" type="time" value={till} onChange={(event) => setTill(event.target.value)} />
          </div>
        </div>
        {defaults.can_assign && (
          <div className="field">
            <label htmlFor="visit-employee">Исполнитель</label>
            <select id="visit-employee" value={employeeId} onChange={(event) => setEmployeeId(event.target.value ? Number(event.target.value) : "")}>
              <option value="">Подобрать по району</option>
              {employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="visit-comment">Комментарий</label>
          <input id="visit-comment" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Домофон, подъезд, что за приборы" />
        </div>
        <Segmented
          size="sm"
          value={source}
          options={[
            { value: "phone", label: "Позвонил" },
            { value: "manual", label: "Пришёл / другое" },
          ]}
          onChange={setSource}
        />
        {error && <Alert>{error}</Alert>}
      </form>
    </Modal>
  );
}
