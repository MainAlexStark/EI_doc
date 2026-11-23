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
import Icon from "../ui/Icon";
import { Alert } from "../ui/controls";
import MonthCalendar, { type CalendarMarker } from "./Calendar";
import { formatPhoneInput, isPhoneComplete } from "../phone";
import { loadSmartCaptcha, type SmartCaptchaApi } from "../smartCaptcha";
import { FIRM, telHref } from "../landing/firm";
import "../landing/form.css";

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

const CONSENT_TEXT =
  "Я даю ООО «Единица Измерения» согласие на обработку указанных в этой форме " +
  "персональных данных (имя или наименование организации, телефон, email, адрес " +
  "объекта поверки) в целях обработки настоящей заявки и связи со мной по вопросам " +
  "поверки средств измерений — сбор, систематизацию, хранение, использование, " +
  "уточнение и удаление, в том числе с использованием средств автоматизации, " +
  "в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных " +
  "данных». Согласие действует до его отзыва по запросу, направленному любым " +
  "удобным способом.";

// Время визита для счётчиков — окнами по 2 часа (так время и называют клиенту).
const TIME_WINDOWS: { start: string; end: string }[] = [
  { start: "08:00", end: "10:00" },
  { start: "10:00", end: "12:00" },
  { start: "12:00", end: "14:00" },
  { start: "14:00", end: "16:00" },
  { start: "16:00", end: "18:00" },
  { start: "18:00", end: "20:00" },
];

type WindowOption = { start: string; end: string; available: boolean; priority: boolean };

/** Окно доступно, если его покрывает слот поверителя (слот без времени — весь день). */
function windowsForDay(daySlots: PublicSlot[], hasSchedule: boolean): WindowOption[] {
  return TIME_WINDOWS.map((w) => {
    if (!hasSchedule) return { ...w, available: true, priority: false };
    const covering = daySlots.filter((s) =>
      !s.start_time || !s.end_time ? true : s.start_time < w.end && s.end_time > w.start,
    );
    return { ...w, available: covering.length > 0, priority: covering.some((s) => s.is_priority) };
  });
}

const money = (value: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value);

/** Сколько быстрых кнопок приборов показать до списка «другой прибор». Счётчики — всегда первыми. */
const QUICK = 4;

type FieldKey = "items" | "address" | "date" | "time" | "name" | "phone" | "email" | "consent" | "captcha";
type Errors = Partial<Record<FieldKey, string>>;
/** Порядок полей в форме — к первой ошибке и прокручиваем. */
const ORDER: FieldKey[] = ["items", "address", "date", "time", "name", "phone", "email", "consent", "captcha"];
const FIELD_ID: Record<FieldKey, string> = {
  items: "rf-items", address: "rf-address", date: "rf-date", time: "rf-time", name: "rf-name",
  phone: "rf-phone", email: "rf-email", consent: "rf-consent", captcha: "rf-captcha",
};

/** Маска телефона; вставку «8 900 …» поверх подставленного «+7» не превращаем в +7 (890…). */
function phoneMask(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.length > 11 && raw.trim().startsWith("+7") && /^7[78]/.test(digits)) return formatPhoneInput(digits.slice(1));
  return formatPhoneInput(raw);
}

/** Когда перезвоним: в рабочее время — скоро, иначе — утром ближайшего рабочего дня. */
function callbackText(now = new Date()): string {
  const day = now.getDay();
  const hour = now.getHours();
  const workday = day >= 1 && day <= 5;
  if (workday && hour >= 8 && hour < 16) return "Позвоним в течение рабочего дня";
  if (workday && hour < 8) return "Позвоним сегодня утром, после 8:00";
  const next = day === 5 || day === 6 ? "в понедельник" : "завтра";
  return `Позвоним ${next} утром, после 8:00`;
}

/** Публичная форма заявки — /zayavka/ и встроенная в одностраничник (/o-nas/).
 *  Порядок: что поверить (счётчики первыми) → адрес → когда (окна по 2 часа) → кто вы.
 *  Обязательно — минимум: прибор, адрес, имя, телефон (или email), согласие. */
export default function PublicRequestForm({ embedded = false }: { embedded?: boolean } = {}) {
  const pageClass = embedded ? "public-embed" : "public-page";
  const brand = embedded ? null : (
    <a className="public-brand" href="/o-nas/">
      <div className="brand-mark">ЕИ</div>
      <div className="brand-name">Единица Измерения<small>метрологическая служба, г. Киров</small></div>
    </a>
  );
  const [form, setForm] = useState<ContactState>(EMPTY);
  const [consentGiven, setConsentGiven] = useState(false);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [addressConfirmed, setAddressConfirmed] = useState(false);
  const [suggestConfigured, setSuggestConfigured] = useState(true);
  const [showList, setShowList] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [withEmail, setWithEmail] = useState(false);
  const [withComment, setWithComment] = useState(false);
  const [doneId, setDoneId] = useState<number | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>();
  const doneRef = useRef<HTMLDivElement>(null);

  // --- приборы: справочник + выбранные позиции ---
  const [families, setFamilies] = useState<Family[]>([]);
  const [discountPercent, setDiscountPercent] = useState(0);
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
          callback: (token: string) => {
            setCaptchaToken(token);
            setErrors((current) => ({ ...current, captcha: undefined }));
          },
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
      setActiveSuggestion(-1);
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

  useEffect(() => {
    if (doneId !== null) doneRef.current?.focus();
  }, [doneId]);

  // Счётчики (им нужно время визита) — первыми, дальше как в справочнике.
  const ordered = useMemo(
    () => [...families].sort((a, b) => Number(b.requires_time_slot) - Number(a.requires_time_slot)),
    [families],
  );
  const quick = ordered.slice(0, QUICK);
  const rest = ordered.slice(QUICK);

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
  const windowsForChosenDate = useMemo(() => {
    if (!chosenDate) return [];
    return windowsForDay(slotsByDate.get(chosenDate) ?? [], availableDates.length > 0);
  }, [slotsByDate, chosenDate, availableDates]);
  const calendarHasPriority = useMemo(() => slots.some((s) => s.is_priority), [slots]);
  const calendarMarkers = useMemo(() => {
    const map: Record<string, CalendarMarker> = {};
    for (const [date, daySlots] of slotsByDate) {
      map[date] = { priority: daySlots.some((s) => s.is_priority) };
    }
    return map;
  }, [slotsByDate]);

  const clearError = (...keys: FieldKey[]) =>
    setErrors((current) => {
      if (!keys.some((k) => current[k])) return current;
      const next = { ...current };
      keys.forEach((k) => delete next[k]);
      return next;
    });

  const toggleItem = (familyId: number) => {
    clearError("items");
    setItems((current) =>
      current.some((item) => item.family_id === familyId)
        ? current.filter((item) => item.family_id !== familyId)
        : [...current, { family_id: familyId, quantity: 1 }],
    );
  };

  const addItem = (value: string) => {
    const familyId = Number(value);
    if (!familyId) return;
    clearError("items");
    setItems((current) => {
      if (current.some((item) => item.family_id === familyId)) return current;
      return [...current, { family_id: familyId, quantity: 1 }];
    });
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
    clearError("address");
    setAddressConfirmed(true);
    setShowList(false);
    setChosenDate("");
    setChosenTime("");
    setChosenPriority(false);
  };

  const update = (patch: Partial<ContactState>) => {
    setForm((current) => ({ ...current, ...patch }));
    if ("address" in patch) {
      setAddressConfirmed(false);
      clearError("address");
    }
    if ("contact_name" in patch) clearError("name");
    if ("contact_phone" in patch || "contact_email" in patch) clearError("phone", "email");
  };

  const pickDate = (date: string) => {
    // Если у района есть график — выбирать можно только отмеченные дни,
    // иначе диспетчеру придётся согласовывать дату, которую никто не подтверждал.
    if (availableDates.length > 0 && !slotsByDate.has(date)) return;
    const next = chosenDate === date ? "" : date; // повторный клик снимает выбор
    clearError("date", "time");
    setChosenDate(next);
    setChosenTime("");
    // Для счётчиков приоритет определяется выбранным окном (pickWindow):
    // слот поверителя «весь день» покрывает все окна, поэтому скидка
    // доступна и без времени в графике. Для остальных приборов — днём.
    const slotsForDate = next ? slotsByDate.get(next) ?? [] : [];
    setChosenPriority(!needsTime && slotsForDate.some((s) => s.is_priority));
  };

  const pickWindow = (window: WindowOption) => {
    if (!window.available) return;
    clearError("time");
    setChosenTime(window.start);
    setChosenPriority(window.priority);
  };

  const validate = (): Errors => {
    const found: Errors = {};
    if (items.length === 0) found.items = "Выберите, что поверить — хотя бы один прибор";
    if (!form.address.trim()) found.address = "Укажите адрес, где стоят приборы";
    if (needsTime && !chosenDate) found.date = "Для счётчиков выберите день визита";
    else if (needsTime && !chosenTime) found.time = "Выберите удобное окно — поверитель приедет в эти 2 часа";
    if (!form.contact_name.trim()) found.name = "Как к вам обращаться?";
    const phone = form.contact_phone.trim();
    const email = form.contact_email.trim();
    if (!phone && !email) found.phone = "Нужен телефон — по нему согласуем время";
    else if (phone && !isPhoneComplete(phone)) found.phone = "Номер неполный — нужно 10 цифр после +7";
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) found.email = "Проверьте email — похоже, в нём опечатка";
    if (!consentGiven) found.consent = "Без согласия на обработку данных мы не можем принять заявку";
    if (captchaConfigured && !captchaToken) found.captcha = "Подтвердите, что вы не робот";
    return found;
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    const found = validate();
    setErrors(found);
    const first = ORDER.find((key) => found[key]);
    if (first) {
      const el = document.getElementById(FIELD_ID[first]);
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
      if (el && "focus" in el) (el as HTMLElement).focus({ preventScroll: true });
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
      consent_given: consentGiven,
      captcha_token: captchaToken || undefined,
    };

    setSubmitting(true);
    try {
      const result = await submitRequest(payload);
      setDoneId(result.id);
    } catch (exc) {
      const offline = typeof navigator !== "undefined" && !navigator.onLine;
      setError(offline
        ? "Нет связи с интернетом — заявка не ушла. Проверьте связь и нажмите «Отправить» ещё раз."
        : exc instanceof Error ? exc.message : "Не удалось отправить заявку");
      // токен капчи одноразовый — после неудачи просим пройти её заново
      if (captchaWidgetId.current !== null && captchaApiRef.current) {
        captchaApiRef.current.reset(captchaWidgetId.current);
      }
      setCaptchaToken("");
    } finally {
      setSubmitting(false);
    }
  };

  const reset = () => {
    setForm(EMPTY);
    setItems([]);
    setChosenDate("");
    setChosenTime("");
    setChosenPriority(false);
    setConsentGiven(false);
    setErrors({});
    setDoneId(null);
  };

  /** Свойства доступности для поля с ошибкой. */
  const invalid = (key: FieldKey) =>
    errors[key] ? { "aria-invalid": true as const, "aria-describedby": `${FIELD_ID[key]}-err` } : {};
  const fieldError = (key: FieldKey) =>
    errors[key] ? <p className="field-error" id={`${FIELD_ID[key]}-err`}><Icon name="alert" size={13} /> {errors[key]}</p> : null;
  const errorCount = Object.values(errors).filter(Boolean).length;

  if (doneId !== null) {
    const slot = needsTime && chosenTime ? TIME_WINDOWS.find((w) => w.start === chosenTime) : null;
    const dayText = chosenDate
      ? new Date(`${chosenDate}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })
      : "";
    return (
      <div className={pageClass}>
        {brand}
        <div className="public-card thanks" ref={doneRef} tabIndex={-1} role="status" aria-live="polite">
          <div className="empty-icon"><Icon name="check" size={28} strokeWidth={2.4} /></div>
          <h1>Заявка принята</h1>
          <p className="thanks-number">№ <b>{doneId}</b></p>
          {dayText && (
            <p className="sub">Вы выбрали: {dayText}{slot ? `, ${slot.start}–${slot.end}` : ""}</p>
          )}
          <ol className="thanks-next">
            <li><b>{callbackText()}</b> — уточним приборы и подтвердим время.</li>
            <li><b>Поверитель приедет</b>{slot ? " в выбранное окно" : " в согласованное время"} и проверит приборы на месте.</li>
            <li><b>Результат — в ФГИС «Аршин»</b>: это и есть документ о поверке.</li>
          </ol>
          <p className="sub">
            Назовите номер заявки, если будете звонить:{" "}
            <a className="thanks-phone" href={telHref()}>{FIRM.phone}</a>
          </p>
          <button type="button" className="link" onClick={reset}>Оставить ещё одну заявку</button>
        </div>
      </div>
    );
  }

  return (
    <div className={pageClass}>
      {brand}
      <form className="public-card" onSubmit={submit} autoComplete="on" noValidate aria-labelledby="rf-title">
        <div>
          <h1 id="rf-title">Заявка на поверку</h1>
          <p className="sub" style={{ marginTop: 6 }}>
            Минута на заполнение — остальное уточним по телефону.
          </p>
        </div>

        {error && <Alert>{error}</Alert>}
        <div className="sr-only" aria-live="polite">{errorCount ? `Проверьте форму: ошибок — ${errorCount}` : ""}</div>

        <fieldset className="rf-step" id="rf-items" tabIndex={-1} {...invalid("items")}>
          <legend><span className="rf-num">1</span>Что поверить<span className="req">*</span></legend>
          {families.length === 0 ? (
            <div className="rf-quick" aria-busy="true">
              {[0, 1, 2, 3].map((i) => <span key={i} className="rf-quick-skel" />)}
            </div>
          ) : (
            <div className="rf-quick">
              {quick.map((family) => {
                const on = items.some((item) => item.family_id === family.id);
                return (
                  <button
                    type="button"
                    key={family.id}
                    className={`rf-chip ${on ? "on" : ""}`}
                    aria-pressed={on}
                    onClick={() => toggleItem(family.id)}
                  >
                    <span className="rf-chip-mark" aria-hidden="true">{on ? "✓" : "+"}</span>
                    <span className="rf-chip-name">{family.name}</span>
                    {Number(family.price) > 0 && <span className="rf-chip-price">от {money(Number(family.price))} ₽</span>}
                  </button>
                );
              })}
            </div>
          )}
          {rest.length > 0 && (
            <select
              id="rf-add-item"
              className="item-picker"
              value=""
              aria-label="Другой прибор"
              onChange={(event) => addItem(event.target.value)}
              disabled={rest.every((family) => items.some((item) => item.family_id === family.id))}
            >
              <option value="">+ другой прибор: манометр, весы, термометр…</option>
              {rest
                .filter((family) => !items.some((item) => item.family_id === family.id))
                .map((family) => (
                  <option key={family.id} value={family.id}>
                    {family.name}
                    {Number(family.price) > 0 ? ` — от ${money(Number(family.price))} ₽` : ""}
                  </option>
                ))}
            </select>
          )}
          {fieldError("items")}

          {items.length > 0 && (
            <ul className="item-list" aria-label="Выбранные приборы">
              {items.map((item) => {
                const family = familyById.get(item.family_id);
                if (!family) return null;
                return (
                  <li key={item.family_id}>
                    <span className="name">{family.name}</span>
                    <span className="qty" role="group" aria-label={`Количество: ${family.name}`}>
                      <button type="button" aria-label="Меньше" onClick={() => setQuantity(item.family_id, Math.max(1, item.quantity - 1))}>−</button>
                      <input
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={99}
                        value={item.quantity}
                        aria-label="Количество, шт."
                        onChange={(event) => setQuantity(item.family_id, Number(event.target.value))}
                      />
                      <button type="button" aria-label="Больше" onClick={() => setQuantity(item.family_id, Math.min(99, item.quantity + 1))}>+</button>
                    </span>
                    <span className="sub price">{money(Number(family.price) * item.quantity)} ₽</span>
                    <button type="button" className="icon-btn sm danger" aria-label={`Убрать: ${family.name}`} title="Убрать" onClick={() => removeItem(item.family_id)}>
                      <Icon name="x" size={14} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {items.length > 0 && pricing.total > 0 && (
            <div className="price-box" aria-live="polite">
              <div>
                <span>Примерно</span>
                <b>{money(pricing.total)} ₽</b>
              </div>
              {pricing.applyDiscount && (
                <div className="sub">
                  скидка {discountPercent}% за приоритетное время: −{money(pricing.discountAmount)} ₽ (было {money(pricing.subtotal)} ₽)
                </div>
              )}
              <div className="hint">Точная цена — после осмотра прибора</div>
            </div>
          )}
        </fieldset>

        <fieldset className="rf-step">
          <legend><span className="rf-num">2</span>Где</legend>
          <div className="field suggest-field">
            <label htmlFor="rf-address">Адрес, где стоят приборы<span className="req">*</span></label>
            <input
              id="rf-address"
              value={form.address}
              onChange={(event) => update({ address: event.target.value })}
              onFocus={() => setShowList(suggestions.length > 0)}
              onBlur={() => setTimeout(() => setShowList(false), 150)}
              onKeyDown={(event) => {
                if (!showList || suggestions.length === 0) return;
                if (event.key === "ArrowDown") { event.preventDefault(); setActiveSuggestion((i) => Math.min(suggestions.length - 1, i + 1)); }
                else if (event.key === "ArrowUp") { event.preventDefault(); setActiveSuggestion((i) => Math.max(0, i - 1)); }
                else if (event.key === "Enter" && activeSuggestion >= 0) { event.preventDefault(); pick(suggestions[activeSuggestion]); }
                else if (event.key === "Escape") setShowList(false);
              }}
              placeholder="Киров, ул. Ленина, 1, кв. 5"
              autoComplete="off"
              role="combobox"
              aria-expanded={showList}
              aria-controls="rf-address-list"
              aria-autocomplete="list"
              aria-activedescendant={activeSuggestion >= 0 ? `rf-sug-${activeSuggestion}` : undefined}
              {...invalid("address")}
            />
            {showList && (
              <ul className="suggestions" id="rf-address-list" role="listbox">
                {suggestions.map((item, index) => (
                  <li
                    key={item.value}
                    id={`rf-sug-${index}`}
                    role="option"
                    aria-selected={index === activeSuggestion}
                    className={index === activeSuggestion ? "active" : ""}
                    onMouseDown={() => pick(item)}
                  >
                    <span>{item.value}</span>
                    {item.postal_code && <span className="sub">{item.postal_code}</span>}
                  </li>
                ))}
              </ul>
            )}
            {fieldError("address")}
            {!suggestConfigured && (
              <div className="hint">Подсказка адресов недоступна — введите адрес полностью</div>
            )}
            {addressConfirmed && <div className="hint ok"><Icon name="check" size={12} /> Адрес найден{form.district ? ` · ${form.district}` : ""}</div>}
          </div>
        </fieldset>

        {items.length > 0 && (
          <fieldset className="rf-step" id="rf-date" tabIndex={-1} {...invalid("date")}>
            <legend>
              <span className="rf-num">3</span>
              {needsTime ? "Когда приехать" : "Желаемый день"}
              {needsTime ? <span className="req">*</span> : <span className="opt"> — необязательно</span>}
            </legend>

            <MonthCalendar
              className="lg"
              selected={chosenDate ? [chosenDate] : []}
              onToggle={pickDate}
              markers={calendarMarkers}
              restrictToMarkers={availableDates.length > 0}
            />
            {fieldError("date")}

            {chosenDate && needsTime && (
              <div className="slot-grid" role="radiogroup" aria-label="Окно визита, 2 часа" id="rf-time" tabIndex={-1} {...invalid("time")}>
                {windowsForChosenDate.map((option) => (
                  <button
                    type="button"
                    key={option.start}
                    role="radio"
                    aria-checked={chosenTime === option.start}
                    disabled={!option.available}
                    className={`slot-chip ${chosenTime === option.start ? "active" : ""} ${option.priority ? "priority" : ""}`}
                    onClick={() => pickWindow(option)}
                    title={option.priority ? "приоритетное время поверителя — со скидкой" : undefined}
                  >
                    {option.start}–{option.end}
                    {option.priority && <span className="dot" aria-label="со скидкой" />}
                  </button>
                ))}
              </div>
            )}
            {fieldError("time")}

            {calendarHasPriority && (
              <div className="hint priority-legend">
                <span className="dot" /> приоритетные дни и время поверителей
                {discountPercent > 0 && needsTime ? ` — скидка ${discountPercent}%` : ""}
              </div>
            )}
            {pricing.applyDiscount && chosenDate && (
              <div className="hint ok">Приоритетное время — скидка {discountPercent}% учтена в цене</div>
            )}
            <div className="hint">
              {districtKnown
                ? availableDates.length > 0
                  ? "Отмечены дни, когда поверители работают в вашем районе"
                  : "Свободных дней в графике пока нет — день согласуем по телефону"
                : form.address
                  ? "Точный день подтвердим по телефону"
                  : "Укажите адрес — покажем дни, когда поверители в вашем районе"}
            </div>
          </fieldset>
        )}

        <fieldset className="rf-step">
          <legend><span className="rf-num">{items.length > 0 ? 4 : 3}</span>Как с вами связаться</legend>
          <div className="field-row">
            <div className="field">
              <label htmlFor="rf-name">Имя или организация<span className="req">*</span></label>
              <input
                id="rf-name"
                name="name"
                autoComplete="name"
                value={form.contact_name}
                onChange={(event) => update({ contact_name: event.target.value })}
                placeholder="Иван Петров"
                {...invalid("name")}
              />
              {fieldError("name")}
            </div>
            <div className="field">
              <label htmlFor="rf-phone">Телефон{!withEmail && <span className="req">*</span>}</label>
              <input
                id="rf-phone"
                name="tel"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                value={form.contact_phone}
                onChange={(event) => update({ contact_phone: phoneMask(event.target.value) })}
                onFocus={() => { if (!form.contact_phone) update({ contact_phone: "+7" }); }}
                onBlur={() => { if (form.contact_phone === "+7") update({ contact_phone: "" }); }}
                placeholder="+7 (900) 123-45-67"
                maxLength={18}
                {...invalid("phone")}
              />
              {fieldError("phone")}
            </div>
          </div>
          {withEmail ? (
            <div className="field">
              <label htmlFor="rf-email">Email<span className="opt"> — если неудобно по телефону</span></label>
              <input
                id="rf-email"
                name="email"
                type="email"
                inputMode="email"
                autoComplete="email"
                value={form.contact_email}
                onChange={(event) => update({ contact_email: event.target.value })}
                placeholder="name@company.ru"
                {...invalid("email")}
              />
              {fieldError("email")}
            </div>
          ) : null}
          <div className="rf-more">
            {!withEmail && <button type="button" className="link" onClick={() => setWithEmail(true)}>+ email</button>}
            {!withComment && <button type="button" className="link" onClick={() => setWithComment(true)}>+ комментарий</button>}
          </div>
          {withComment && (
            <div className="field">
              <label htmlFor="rf-comment">Комментарий<span className="opt"> — необязательно</span></label>
              <textarea
                id="rf-comment"
                rows={3}
                value={form.comment}
                onChange={(event) => update({ comment: event.target.value })}
                placeholder="Код домофона, сколько приборов, нужен ли договор…"
                autoFocus
              />
            </div>
          )}
        </fieldset>

        <div className="consent">
          <label>
            <input
              id="rf-consent"
              type="checkbox"
              checked={consentGiven}
              onChange={(event) => { setConsentGiven(event.target.checked); clearError("consent"); }}
              {...invalid("consent")}
            />
            <span>
              Согласен на обработку персональных данных по 152-ФЗ<span className="req"> *</span>
            </span>
          </label>
          {fieldError("consent")}
          <details>
            <summary>Текст согласия</summary>
            <p>{CONSENT_TEXT}</p>
          </details>
        </div>

        <div className="field captcha-field" id="rf-captcha" tabIndex={-1} style={{ display: captchaConfigured ? "block" : "none" }}>
          <div ref={captchaContainerRef} className="captcha-widget" />
          {fieldError("captcha")}
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

        <button className="primary" type="submit" disabled={submitting} aria-busy={submitting}>
          {submitting ? "Отправляю…" : "Отправить заявку"}
        </button>
        <p className="hint rf-or">
          или позвоните: <a href={telHref()}>{FIRM.phone}</a> · {FIRM.hours}
        </p>
      </form>
      {!embedded && (
        <div className="public-foot">
          <a href="/o-nas/">ООО «Единица Измерения»</a> · поверка средств измерений в Кирове и области
        </div>
      )}
    </div>
  );
}
