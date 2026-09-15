import { useCallback, useEffect, useState } from "react";
import {
  createAvailability,
  deleteAvailability,
  fetchMyAvailability,
  requestTelegramLinkCode,
  type AvailabilityPayload,
  type AvailabilitySlot,
  type TelegramLinkInfo,
} from "../api";

const formatDate = (value: string) => new Date(value).toLocaleDateString("ru-RU");

const KIND_LABELS: Record<string, string> = { district: "по своему району", trip: "командировка" };

function TelegramLinkBox() {
  const [info, setInfo] = useState<TelegramLinkInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const request = async () => {
    setBusy(true);
    setError("");
    try {
      setInfo(await requestTelegramLinkCode());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось получить код");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="telegram-box">
      <h3>Уведомления в Telegram</h3>
      {!info && (
        <>
          <p className="sub">
            Получайте уведомления о новых нарядах и задачах в Telegram — привяжите чат одноразовым кодом.
          </p>
          <button type="button" disabled={busy} onClick={() => void request()}>
            {busy ? "Получаю…" : "Получить код привязки"}
          </button>
        </>
      )}
      {info && (
        <div className="telegram-code">
          <p>
            {info.already_linked && "Telegram уже привязан — этот код перепривяжет чат заново. "}
            Откройте {info.bot_username ? `бота @${info.bot_username}` : "бота EI_doc"} в Telegram и пришлите:
          </p>
          <code>/start {info.code}</code>
          <p className="sub">
            Код действует до {new Date(info.expires_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}
          </p>
          <button type="button" disabled={busy} onClick={() => void request()}>
            Получить новый код
          </button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function NewSlotForm({ onCreated }: { onCreated: () => void }) {
  const [date, setDate] = useState("");
  const [kind, setKind] = useState<"district" | "trip">("district");
  const [allDay, setAllDay] = useState(true);
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("18:00");
  const [isPriority, setIsPriority] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!date) {
      setError("Укажите дату");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload: AvailabilityPayload = {
        kind,
        date,
        start_time: allDay ? null : startTime,
        end_time: allDay ? null : endTime,
        is_priority: isPriority,
        note,
      };
      await createAvailability(payload);
      setDate("");
      setNote("");
      setIsPriority(false);
      onCreated();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось добавить слот");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="filters" onSubmit={submit}>
      <div className="field">
        <label htmlFor="av-date">Дата</label>
        <input id="av-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="av-kind">Тип</label>
        <select id="av-kind" value={kind} onChange={(e) => setKind(e.target.value as "district" | "trip")}>
          <option value="district">выезд в своём районе</option>
          <option value="trip">командировка</option>
        </select>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 20 }}>
        <input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} /> весь день
      </label>
      {!allDay && (
        <>
          <div className="field">
            <label htmlFor="av-start">С</label>
            <input id="av-start" type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="av-end">По</label>
            <input id="av-end" type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
          </div>
        </>
      )}
      <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 20 }} title="Заявителю на сайте это время показывается со скидкой">
        <input type="checkbox" checked={isPriority} onChange={(e) => setIsPriority(e.target.checked)} /> приоритетное для меня
      </label>
      <div className="field">
        <label htmlFor="av-note">Примечание</label>
        <input id="av-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="необязательно" />
      </div>
      <button className="primary" type="submit" disabled={busy}>
        Добавить
      </button>
      {error && <span className="error" style={{ marginBottom: 0 }}>{error}</span>}
    </form>
  );
}

const weekRange = (): [string, string] => {
  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const end = new Date(today);
  end.setDate(today.getDate() + 30);
  return [iso(today), iso(end)];
};

/** Личный график сотрудника — когда он может выезжать в своём районе, командировки,

 * приоритетное для себя время. Публичная форма заявки берёт эти слоты, чтобы предложить
 * заявителю дату/время (apps.hub.api_availability.AvailabilityPublicSlotsView).
 */
export default function Availability() {
  const [range, setRange] = useState<[string, string]>(weekRange());
  const [slots, setSlots] = useState<AvailabilitySlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = await fetchMyAvailability({ date_from: range[0], date_to: range[1] });
      setSlots(page.results);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить график");
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (slot: AvailabilitySlot) => {
    setBusy(slot.id);
    try {
      await deleteAvailability(slot.id);
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось удалить слот");
    } finally {
      setBusy(null);
    }
  };

  const byDate = new Map<string, AvailabilitySlot[]>();
  for (const slot of slots) {
    const list = byDate.get(slot.date) ?? [];
    list.push(slot);
    byDate.set(slot.date, list);
  }

  return (
    <>
      <TelegramLinkBox />

      <h3>Мой график</h3>
      <NewSlotForm onCreated={() => void load()} />

      <div className="filters">
        <div className="field">
          <label htmlFor="av-from">С</label>
          <input id="av-from" type="date" value={range[0]} onChange={(e) => setRange([e.target.value, range[1]])} />
        </div>
        <div className="field">
          <label htmlFor="av-to">по</label>
          <input id="av-to" type="date" value={range[1]} onChange={(e) => setRange([range[0], e.target.value])} />
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Загружаю…</div>}

      {!loading && byDate.size === 0 && <div className="empty">В этом диапазоне слотов нет</div>}

      {!loading && [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, daySlots]) => (
        <div className="calendar-day" key={day}>
          <h4>{formatDate(day)}</h4>
          <ul>
            {daySlots.map((slot) => (
              <li key={slot.id}>
                {slot.start_time && slot.end_time ? `${slot.start_time}–${slot.end_time}` : "весь день"}
                {" · "}
                {KIND_LABELS[slot.kind] ?? slot.kind}
                {slot.is_priority && <span className="pill ok" style={{ marginLeft: 6 }}>приоритет</span>}
                {slot.note && <span className="sub"> — {slot.note}</span>}
                <button style={{ marginLeft: 10 }} disabled={busy === slot.id} onClick={() => void remove(slot)}>
                  Удалить
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}
