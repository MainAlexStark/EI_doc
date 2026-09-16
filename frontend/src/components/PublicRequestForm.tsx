import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCaptchaConfig,
  fetchFamilies,
  fetchPublicSlots,
  submitRequest,
  suggestAddress,
  type AddressSuggestion,
  type Family,
  type PublicSlot,
  type RequestItemPayload,
  type RequestPayload,
} from "../api";
import MonthCalendar, { type CalendarMarker } from "./Calendar";
import { formatPhoneInput, isPhoneComplete } from "../phone";
import { loadSmartCaptcha, type SmartCaptchaApi } from "../smartCaptcha";

type ContactState = {
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  address: string;
  postal_code?: string;
  fias_id?: string;
  district?: string;
  latitude?: number | null;
  longitude?: number | null;
  comment: string;
  website: string;
};

const EMPTY: ContactState = {
  contact_name: "",
  contact_phone: "",
  contact_email: "",
  address: "",
  comment: "",
  website: "",
};

const money = (value: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value);

/** Публичная форма заявки — /zayavka/. Без авторизации, без вкладок EI_doc. */
export default function PublicRequestForm() {
  const [form, setForm] = useState<ContactState>(EMPTY);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [addressConfirmed, setAddressConfirmed] = useState(false);
  const [suggestConfigured, setSuggestConfigured] = useState(true);
  const [showList, setShowList] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [doneId, setDoneId] = useState<number | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  // --- приборы: справочник + выбранные позиции ---
  const [families, setFamilies] = useState<Family[]>([]);
  const [discountPercent, setDiscountPercent] = useState(0);
  const [addFamilyId, setAddFamilyId] = useState("");
  const [items, setItems] = useState<RequestItemPayload[]>([]);

  // --- дата/время выезда: слоты из графика сотрудников по району ---
  const [slots, setSlots] = useState<PublicSlot[]>([]);
  const [districtKnown, setDistrictKnown] = useState(false);
  const [chosenDate, setChosenDate] = useState("");
  const [chosenTime, setChosenTime] = useState("");
  const [chosenPriority, setChosenPriority] = useState(false);

  // --- капча (Yandex SmartCaptcha) ---
  const [captchaConfigured, setCaptchaConfigured] = useState(false);
  const [captchaToken, setCaptchaToken] = useState("");
  const captchaContainerRef = useRef<HTMLDivElement | null>(null);
  const captchaWidgetId = useRef<number | null>(null);
  const captchaApiRef = useRef<SmartCaptchaApi | null>(null);

  useEffect(() => {
    void fetchFamilies().then((response) => {
      setFamilies(response.families);
      setDiscountPercent(Number(response.priority_discount_percent) || 0);
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchCaptchaConfig().then((config) => {
      if (cancelled || !config.configured || !config.client_key) return;
      setCaptchaConfigured(true);
      void loadSmartCaptcha().then((api) => {
        if (cancelled || !captchaContainerRef.current) return;
        captchaApiRef.current = api;
        captchaWidgetId.current = api.render(captchaContainerRef.current, {
          sitekey: config.client_key,
          hl: "ru",
          callback: (token: string) => setCaptchaToken(token),
        });
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

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

  useEffect(() => {
    const district = form.district ?? "";
    if (!district) {
      setSlots([]);
      setDistrictKnown(false);
      return;
    }
    void fetchPublicSlots(district).then((response) => {
      setSlots(response.slots);
      setDistrictKnown(response.district_known);
    });
  }, [form.district]);

  const familyById = useMemo(() => new Map(families.map((f) => [f.id, f])), [families]);
  const chosenFamilies = useMemo(
    () => items.map((item) => familyById.get(item.family_id)).filter((f): f is Family => Boolean(f)),
    [items, familyById],
  );
  const needsTime = chosenFamilies.some((f) => f.requires_time_slot);

  const pricing = useMemo(() => {
    const subtotal = items.reduce((sum, item) => {
      const family = familyById.get(item.family_id);
      return sum + (family ? Number(family.price) * item.quantity : 0);
    }, 0);
    const applyDiscount = needsTime && chosenPriority;
    const discountAmount = applyDiscount ? Math.round(subtotal * discountPercent) / 100 : 0;
    return { subtotal, discountAmount, total: subtotal - discountAmount, applyDiscount };
  }, [items, familyById, needsTime, chosenPriority, discountPercent]);

  // Слоты, сгруппированные по дате — своя дата может встречаться у нескольких сотрудников.
  const slotsByDate = useMemo(() => {
    const map = new Map<string, PublicSlot[]>();
    for (const slot of slots) {
      const list = map.get(slot.date) ?? [];
      list.push(slot);
      map.set(slot.date, list);
    }
    return map;
  }, [slots]);
  const availableDates = useMemo(() => [...slotsByDate.keys()].sort(), [slotsByDate]);
  const timesForChosenDate = useMemo(() => {
    if (!chosenDate) return [];
    return (slotsByDate.get(chosenDate) ?? []).filter((s) => s.start_time && s.end_time);
  }, [slotsByDate, chosenDate]);
  // Для календаря — какие дни отметить (есть слот в графике сотрудников района).
  const calendarMarkers = useMemo(() => {
    const map: Record<string, CalendarMarker> = {};
    for (const [date, daySlots] of slotsByDate) {
      map[date] = { priority: daySlots.some((s) => s.is_priority) };
    }
    return map;
  }, [slotsByDate]);

  const addItem = () => {
    if (!addFamilyId) return;
    const familyId = Number(addFamilyId);
    setItems((current) => {
      if (current.some((item) => item.family_id === familyId)) return current;
      return [...current, { family_id: familyId, quantity: 1 }];
    });
    setAddFamilyId("");
  };

  const setQuantity = (familyId: number, quantity: number) => {
    const safe = Number.isFinite(quantity) ? Math.max(1, Math.min(99, quantity)) : 1;
    setItems((current) =>
      current.map((item) => (item.family_id === familyId ? { ...item, quantity: safe } : item)),
    );
  };

  const removeItem = (familyId: number) => {
    setItems((current) => current.filter((item) => item.family_id !== familyId));
  };

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
    setChosenDate("");
    setChosenTime("");
    setChosenPriority(false);
  };

  const update = (patch: Partial<ContactState>) => {
    setForm((current) => ({ ...current, ...patch }));
    if ("address" in patch) setAddressConfirmed(false);
  };

  const pickDate = (date: string) => {
    // Если у района есть график — выбирать можно только отмеченные дни,
    // иначе диспетчеру придётся согласовывать дату, которую никто не подтверждал.
    if (availableDates.length > 0 && !slotsByDate.has(date)) return;
    const next = chosenDate === date ? "" : date; // повторный клик снимает выбор
    setChosenDate(next);
    setChosenTime("");
    // Приоритет по умолчанию — если хоть один слот в этот день отмечен
    // сотрудником как приоритетный. Считаем это независимо от needsTime:
    // сотрудники чаще всего заводят слоты "весь день" (без времени, см.
    // Availability.tsx), тогда чипов времени вообще не будет и pickTime()
    // ниже не вызовется ни разу — раньше это означало, что скидка для
    // приборов с обязательным временем не применялась практически никогда.
    // Если время всё же выбирается через чип — pickTime() уточнит по
    // конкретному слоту.
    const slotsForDate = next ? slotsByDate.get(next) ?? [] : [];
    setChosenPriority(slotsForDate.some((s) => s.is_priority));
  };

  const pickTime = (slot: PublicSlot) => {
    setChosenTime(slot.start_time ?? "");
    setChosenPriority(slot.is_priority);
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
    if (form.contact_phone.trim() && !isPhoneComplete(form.contact_phone)) {
      setError("Введите телефон полностью: +7 (900) 123-45-67");
      return;
    }
    if (!form.address.trim()) {
      setError("Укажите адрес поверки");
      return;
    }
    if (items.length === 0) {
      setError("Выберите хотя бы один тип прибора");
      return;
    }
    if (needsTime && !chosenDate) {
      setError("Среди выбранных приборов есть счётчики — укажите дату и время выезда");
      return;
    }
    if (needsTime && chosenDate && !chosenTime) {
      setError("Среди выбранных приборов есть счётчики — уточните время");
      return;
    }
    if (captchaConfigured && !captchaToken) {
      setError("Подтвердите, что вы не робот");
      return;
    }

    const desiredDate = chosenDate || null;
    const desiredTime = needsTime && chosenDate ? chosenTime || null : null;

    const payload: RequestPayload = {
      ...form,
      items,
      desired_date: desiredDate,
      desired_time: desiredTime,
      is_priority_slot: chosenPriority && Boolean(desiredDate),
      is_address_confirmed: addressConfirmed,
      captcha_token: captchaToken || undefined,
    };

    setSubmitting(true);
    try {
      const result = await submitRequest(payload);
      setDoneId(result.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отправить заявку");
      // токен капчи одноразовый — после неудачи просим пройти её заново
      if (captchaWidgetId.current !== null && captchaApiRef.current) {
        captchaApiRef.current.reset(captchaWidgetId.current);
      }
      setCaptchaToken("");
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

        <p className="hint"><span className="req">*</span> — обязательные поля</p>

        <div className="field">
          <label htmlFor="rf-name">Имя или организация<span className="req">*</span></label>
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
              type="tel"
              inputMode="tel"
              value={form.contact_phone}
              onChange={(event) => update({ contact_phone: formatPhoneInput(event.target.value) })}
              placeholder="+7 (900) 123-45-67"
              maxLength={18}
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
        <p className="hint"><span className="req">*</span> телефон или email — укажите хотя бы один</p>

        <div className="field suggest-field">
          <label htmlFor="rf-address">Адрес поверки<span className="req">*</span></label>
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
          <label htmlFor="rf-add-item">Что нужно поверить<span className="req">*</span></label>
          <div className="item-picker">
            <select id="rf-add-item" value={addFamilyId} onChange={(event) => setAddFamilyId(event.target.value)}>
              <option value="">выберите тип прибора…</option>
              {families
                .filter((family) => !items.some((item) => item.family_id === family.id))
                .map((family) => (
                  <option key={family.id} value={family.id}>
                    {family.name}
                    {Number(family.price) > 0 ? ` — от ${money(Number(family.price))} ₽` : ""}
                  </option>
                ))}
            </select>
            <button type="button" onClick={addItem} disabled={!addFamilyId}>
              Добавить
            </button>
          </div>

          {items.length > 0 && (
            <ul className="item-list">
              {items.map((item) => {
                const family = familyById.get(item.family_id);
                if (!family) return null;
                return (
                  <li key={item.family_id}>
                    <span className="name">{family.name}</span>
                    <input
                      type="number"
                      min={1}
                      max={99}
                      value={item.quantity}
                      onChange={(event) => setQuantity(item.family_id, Number(event.target.value))}
                    />
                    <span className="sub">шт.</span>
                    <span className="sub price">{money(Number(family.price) * item.quantity)} ₽</span>
                    <button type="button" onClick={() => removeItem(item.family_id)}>
                      Убрать
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {items.length > 0 && (
          <div className="price-box">
            <div>
              <span>Примерная цена</span>
              <b>{money(pricing.total)} ₽</b>
            </div>
            {pricing.applyDiscount && (
              <div className="sub">
                скидка {discountPercent}% за приоритетное время: −{money(pricing.discountAmount)} ₽
                {" "}(было {money(pricing.subtotal)} ₽)
              </div>
            )}
            <div className="hint">Точная цена — после осмотра прибора на месте</div>
          </div>
        )}

        <div className="field">
          <label>
            {needsTime ? "Дата и время выезда" : "Желаемая дата"}
            {needsTime ? <span className="req">*</span> : <span className="opt"> (необязательно)</span>}
          </label>

          <MonthCalendar
            selected={chosenDate ? [chosenDate] : []}
            onToggle={pickDate}
            markers={calendarMarkers}
            restrictToMarkers={availableDates.length > 0}
          />

          {chosenDate && needsTime && (
            timesForChosenDate.length > 0 ? (
              <div className="slot-grid">
                {timesForChosenDate.map((slot, index) => (
                  <button
                    type="button"
                    key={`${slot.start_time}-${index}`}
                    className={`slot-chip ${chosenTime === slot.start_time ? "active" : ""}`}
                    onClick={() => pickTime(slot)}
                  >
                    {slot.start_time}–{slot.end_time}
                    {slot.is_priority && <span className="dot" title="приоритетное время — скидка" />}
                  </button>
                ))}
              </div>
            ) : (
              <div className="field">
                <label htmlFor="rf-time">Удобное время<span className="req">*</span></label>
                <input
                  id="rf-time"
                  type="time"
                  value={chosenTime}
                  onChange={(event) => setChosenTime(event.target.value)}
                />
              </div>
            )
          )}

          {pricing.applyDiscount && chosenDate && (
            <div className="hint ok">Выбрано приоритетное время — скидка {discountPercent}% учтена в цене</div>
          )}

          <div className="hint">
            {districtKnown
              ? availableDates.length > 0
                ? "Отмеченные дни — из графика сотрудников вашего района"
                : "На ближайшее время свободных слотов не указано — дату и время сверит диспетчер по телефону"
              : "Точную дату согласует диспетчер по телефону после выбора адреса"}
          </div>
        </div>

        <div className="field">
          <label htmlFor="rf-comment">Комментарий<span className="opt"> (необязательно)</span></label>
          <textarea
            id="rf-comment"
            rows={3}
            value={form.comment}
            onChange={(event) => update({ comment: event.target.value })}
            placeholder="Что-то уточнить об адресе, доступе, приборах..."
          />
        </div>

        <div className="field captcha-field" style={{ display: captchaConfigured ? "block" : "none" }}>
          <div ref={captchaContainerRef} className="captcha-widget" />
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
