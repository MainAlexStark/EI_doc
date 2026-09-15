import { useEffect, useRef, useState } from "react";
import {
  submitRequest,
  suggestAddress,
  type AddressSuggestion,
  type RequestPayload,
} from "../api";

const EMPTY: RequestPayload = {
  contact_name: "",
  contact_phone: "",
  contact_email: "",
  address: "",
  si_description: "",
  desired_date: "",
  comment: "",
  website: "",
};

/** Публичная форма заявки — /zayavka/. Без авторизации, без вкладок EI_doc. */
export default function PublicRequestForm() {
  const [form, setForm] = useState<RequestPayload>(EMPTY);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [addressConfirmed, setAddressConfirmed] = useState(false);
  const [suggestConfigured, setSuggestConfigured] = useState(true);
  const [showList, setShowList] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [doneId, setDoneId] = useState<number | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (addressConfirmed) return; // адрес уже выбран из списка — не переспрашиваем
    const query = form.address ?? "";
    clearTimeout(debounce.current);
    if (query.trim().length < 3) {
      setSuggestions([]);
      return;
    }
    debounce.current = setTimeout(async () => {
      const response = await suggestAddress(query);
      setSuggestConfigured(response.configured);
      setSuggestions(response.results);
      setShowList(response.results.length > 0);
    }, 300);
    return () => clearTimeout(debounce.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.address, addressConfirmed]);

  const pick = (item: AddressSuggestion) => {
    setForm((current) => ({
      ...current,
      address: item.value,
      postal_code: item.postal_code,
      fias_id: item.fias_id,
      district: item.district,
      latitude: item.latitude,
      longitude: item.longitude,
    }));
    setAddressConfirmed(true);
    setShowList(false);
  };

  const update = (patch: Partial<RequestPayload>) => {
    setForm((current) => ({ ...current, ...patch }));
    if ("address" in patch) setAddressConfirmed(false);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    if (!form.contact_name.trim()) {
      setError("Укажите, как к вам обращаться");
      return;
    }
    if (!form.contact_phone?.trim() && !form.contact_email?.trim()) {
      setError("Укажите телефон или email для связи");
      return;
    }
    if (!form.address.trim()) {
      setError("Укажите адрес поверки");
      return;
    }
    setSubmitting(true);
    try {
      const result = await submitRequest({ ...form, is_address_confirmed: addressConfirmed });
      setDoneId(result.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отправить заявку");
    } finally {
      setSubmitting(false);
    }
  };

  if (doneId !== null) {
    return (
      <div className="public-page">
        <div className="public-card thanks">
          <h1>Заявка принята</h1>
          <p>
            Номер заявки — <b>№{doneId}</b>. Мы свяжемся с вами, чтобы согласовать дату
            поверки.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="public-page">
      <form className="public-card" onSubmit={submit} autoComplete="off">
        <h1>Заявка на поверку</h1>
        <p className="sub">ООО «Единица Измерения» — метрологическая служба, г. Киров</p>

        {error && <div className="error">{error}</div>}

        <div className="field">
          <label htmlFor="rf-name">Имя или организация</label>
          <input
            id="rf-name"
            value={form.contact_name}
            onChange={(event) => update({ contact_name: event.target.value })}
            placeholder="Иванов Иван Иванович"
          />
        </div>

        <div className="field-row">
          <div className="field">
            <label htmlFor="rf-phone">Телефон</label>
            <input
              id="rf-phone"
              value={form.contact_phone}
              onChange={(event) => update({ contact_phone: event.target.value })}
              placeholder="+7 900 000-00-00"
            />
          </div>
          <div className="field">
            <label htmlFor="rf-email">Email</label>
            <input
              id="rf-email"
              type="email"
              value={form.contact_email}
              onChange={(event) => update({ contact_email: event.target.value })}
            />
          </div>
        </div>

        <div className="field suggest-field">
          <label htmlFor="rf-address">Адрес поверки</label>
          <input
            id="rf-address"
            value={form.address}
            onChange={(event) => update({ address: event.target.value })}
            onFocus={() => setShowList(suggestions.length > 0)}
            onBlur={() => setTimeout(() => setShowList(false), 150)}
            placeholder="г. Киров, ул. ..."
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
          {!suggestConfigured && (
            <div className="hint">Подсказка адресов недоступна — введите адрес полностью вручную</div>
          )}
          {addressConfirmed && <div className="hint ok">Адрес выбран из справочника</div>}
        </div>

        <div className="field">
          <label htmlFor="rf-si">Что нужно поверить</label>
          <input
            id="rf-si"
            value={form.si_description}
            onChange={(event) => update({ si_description: event.target.value })}
            placeholder="Счётчик холодной воды, 2 шт."
          />
        </div>

        <div className="field">
          <label htmlFor="rf-date">Желаемая дата</label>
          <input
            id="rf-date"
            type="date"
            value={form.desired_date ?? ""}
            onChange={(event) => update({ desired_date: event.target.value })}
          />
        </div>

        <div className="field">
          <label htmlFor="rf-comment">Комментарий</label>
          <textarea
            id="rf-comment"
            rows={3}
            value={form.comment}
            onChange={(event) => update({ comment: event.target.value })}
          />
        </div>

        {/* honeypot: обычному человеку не видно и незачем заполнять */}
        <div className="hp" aria-hidden="true">
          <label htmlFor="rf-website">Сайт</label>
          <input
            id="rf-website"
            tabIndex={-1}
            autoComplete="off"
            value={form.website}
            onChange={(event) => update({ website: event.target.value })}
          />
        </div>

        <button className="primary" type="submit" disabled={submitting}>
          {submitting ? "Отправляю…" : "Отправить заявку"}
        </button>
      </form>
    </div>
  );
}
