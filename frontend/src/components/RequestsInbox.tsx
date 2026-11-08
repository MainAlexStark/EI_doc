import { useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmRequest,
  fetchEmployees,
  fetchRequests,
  rejectRequest,
  type Employee,
  type HubRequest,
} from "../api";
import Icon from "../ui/Icon";
import { confirmDialog, promptDialog } from "../ui/Modal";
import { Alert, EmptyState, SearchField, Segmented, Spinner } from "../ui/controls";
import { PHONE, useMediaQuery } from "../ui/hooks";

const money = (value: string | number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(Number(value));

const STATUS: Record<string, { label: string; tone: string }> = {
  new: { label: "Новая", tone: "info" },
  routed: { label: "Подобран исполнитель", tone: "info" },
  confirmed: { label: "Подтверждена", tone: "ok" },
  done: { label: "Выполнена", tone: "ok" },
  cancelled: { label: "Отменена", tone: "muted" },
  rejected: { label: "Отклонена", tone: "bad" },
  spam: { label: "Спам / дубль", tone: "muted" },
};
const SOURCE: Record<string, string> = { site: "с сайта", phone: "по телефону", email: "по почте", manual: "внесена вручную" };

type Box = "inbox" | "confirmed" | "archive" | "spam";
const BOX_STATUSES: Record<Box, string[]> = {
  inbox: ["new", "routed"],
  confirmed: ["confirmed"],
  archive: ["done", "cancelled", "rejected"],
  spam: ["spam"],
};

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "—";

function receivedAt(value: string): string {
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "вчера";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
}

const itemsLine = (request: HubRequest) =>
  request.items.length
    ? request.items.map((item) => `${item.family}${item.quantity > 1 ? ` × ${item.quantity}` : ""}`).join(", ")
    : request.si_description || "Приборы не указаны";

/** Диспетчерская очередь заявок с сайта: рекомендация → подтверждение → наряд. Как «Почта»:
 * список слева, заявка справа; на телефоне — список, по нажатию заявка. */
export default function RequestsInbox({ onChanged }: { onChanged?: () => void }) {
  const phone = useMediaQuery(PHONE);
  const [box, setBox] = useState<Box>("inbox");
  const [query, setQuery] = useState("");
  const [requests, setRequests] = useState<HubRequest[]>([]);
  const [spam, setSpam] = useState<HubRequest[] | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError("");
    try {
      const [rows, staff] = await Promise.all([fetchRequests({}), fetchEmployees()]);
      setRequests(rows);
      setEmployees(staff);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить заявки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (box === "spam" && spam === null) {
      void fetchRequests({ status: "spam" }).then(setSpam).catch(() => setSpam([]));
    }
  }, [box, spam]);

  const source = box === "spam" ? spam ?? [] : requests;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return source
      .filter((r) => BOX_STATUSES[box].includes(r.status))
      .filter((r) => !q || `${r.id} ${r.contact_name} ${r.contact_phone} ${r.address} ${r.comment}`.toLowerCase().includes(q));
  }, [source, box, query]);

  const counts = useMemo(
    () => ({
      inbox: requests.filter((r) => BOX_STATUSES.inbox.includes(r.status)).length,
      confirmed: requests.filter((r) => r.status === "confirmed").length,
    }),
    [requests],
  );

  // На широком экране сразу открыта первая заявка в ящике — как в «Почте».
  useEffect(() => {
    if (phone || loading) return;
    if (!shown.some((r) => r.id === openId)) setOpenId(shown[0]?.id ?? null);
  }, [shown, phone, loading, openId]);

  const open = shown.find((r) => r.id === openId) ?? source.find((r) => r.id === openId) ?? null;

  const afterChange = async (message?: string) => {
    setSpam(null);
    if (message) setFlash(message);
    await load();
    onChanged?.();
  };

  useEffect(() => {
    if (!flash) return;
    const timer = window.setTimeout(() => setFlash(""), 8000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  if (loading) return <Spinner />;

  return (
    <div className={`split ${open && phone ? "has-detail" : ""}`}>
      <div className="split-list">
        <div className="split-list-head">
          <Segmented
            size="sm"
            value={box}
            onChange={(next) => {
              setBox(next);
              setOpenId(null);
            }}
            options={[
              { value: "inbox", label: "Входящие", count: counts.inbox },
              { value: "confirmed", label: "В работе", count: counts.confirmed },
              { value: "archive", label: "Архив" },
              { value: "spam", label: "Спам" },
            ]}
          />
          <SearchField value={query} onChange={setQuery} placeholder="Имя, телефон, адрес, №" />
        </div>
        <div className="split-list-body">
          {error && <Alert>{error}</Alert>}
          {shown.length === 0 && (
            <EmptyState icon="inbox" title={box === "inbox" ? "Новых заявок нет" : "Пусто"}>
              {box === "inbox" ? "Заявки с сайта появятся здесь — с рекомендованным исполнителем и ценой." : query ? "Под запрос ничего не нашлось" : null}
            </EmptyState>
          )}
          <div className="mail-list">
            {shown.map((request) => (
              <button
                type="button"
                key={request.id}
                className={`mail-item ${request.id === openId ? "active" : ""}`}
                onClick={() => setOpenId(request.id)}
              >
                {request.status === "new" && <span className="unread" aria-label="новая" />}
                <span className="mail-top">
                  <b>{request.contact_name}</b>
                  <span className="mail-date">{receivedAt(request.created_at)}</span>
                </span>
                <span className="sub">{itemsLine(request)}{request.estimated_price ? ` · ${money(request.estimated_price)} ₽` : ""}</span>
                <span className="mail-meta">{request.address}</span>
                {(box !== "inbox" || request.status === "routed" || request.desired_date) && (
                  <span className="mail-tags">
                    {box !== "inbox" && <span className={`pill ${STATUS[request.status]?.tone ?? "muted"}`}>{STATUS[request.status]?.label ?? request.status}</span>}
                    {request.desired_date && <span className="tag"><Icon name="calendar" size={11} />{formatDate(request.desired_date)}{request.desired_time ? `, ${request.desired_time.slice(0, 5)}` : ""}</span>}
                    {request.status === "routed" && request.suggested_employee && <span className="tag info"><Icon name="user" size={11} />{request.suggested_employee}</span>}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="split-detail">
        {flash && (
          <div className="split-detail-inner" style={{ paddingBottom: 0 }}>
            <Alert tone="ok" onClose={() => setFlash("")}>{flash}</Alert>
          </div>
        )}
        {open ? (
          <RequestDetail
            key={open.id}
            request={open}
            employees={employees}
            onBack={phone ? () => setOpenId(null) : undefined}
            onChanged={afterChange}
          />
        ) : (
          !phone && <EmptyState icon="inbox" title="Заявка не выбрана">Выберите заявку в списке слева.</EmptyState>
        )}
      </div>
    </div>
  );
}

function RequestDetail({
  request, employees, onBack, onChanged,
}: {
  request: HubRequest;
  employees: Employee[];
  onBack?: () => void;
  onChanged: (message?: string) => Promise<void>;
}) {
  const [employee, setEmployee] = useState(request.suggested_employee_id?.toString() ?? "");
  const [date, setDate] = useState(request.desired_date ?? "");
  const [time, setTime] = useState(request.desired_time?.slice(0, 5) ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const actionable = request.status === "new" || request.status === "routed";
  const status = STATUS[request.status] ?? { label: request.status, tone: "muted" };

  const confirm = async () => {
    if (!employee) {
      setError("Выберите исполнителя");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await confirmRequest(request.id, {
        employee_id: Number(employee),
        scheduled_date: date || null,
        scheduled_time: time || null,
        note: note || undefined,
      });
      const who = employees.find((e) => String(e.id) === employee)?.full_name ?? "исполнителя";
      await onChanged(`Заявка №${request.id} подтверждена, создан наряд №${result.work_order_id}. Исполнитель — ${who}: выезд уже в его «Поверках» и графике.`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось подтвердить заявку");
    } finally {
      setBusy(false);
    }
  };

  const reject = async (spam: boolean) => {
    let reason = "";
    if (spam) {
      if (!(await confirmDialog({ title: "Пометить как спам?", text: "Заявка уйдёт в «Спам» — её можно будет найти там.", ok: "В спам" }))) return;
    } else {
      const answer = await promptDialog({ title: "Отклонить заявку", text: "Причину увидят коллеги в архиве заявок.", ok: "Отклонить", placeholder: "Например, не наш район" });
      if (answer === null) return;
      reason = answer;
    }
    setBusy(true);
    setError("");
    try {
      await rejectRequest(request.id, { reason, spam });
      await onChanged(spam ? `Заявка №${request.id} перенесена в «Спам».` : `Заявка №${request.id} отклонена.`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отклонить заявку");
    } finally {
      setBusy(false);
    }
  };

  const total = request.items.reduce((sum, item) => sum + Number(item.subtotal), 0);
  const mapUrl = `https://yandex.ru/maps/?text=${encodeURIComponent(request.address)}`;

  return (
    <div className="split-detail-inner">
      {onBack && (
        <button type="button" className="back" onClick={onBack}>
          <Icon name="chevronLeft" size={20} /> Заявки
        </button>
      )}
      <div className="detail-head">
        <div className="titles">
          <div className="sub">
            Заявка №{request.id} · {new Date(request.created_at).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })} · {SOURCE[request.source] ?? request.source}
          </div>
          <h2>{request.contact_name}</h2>
        </div>
        <span className={`pill ${status.tone}`}>{status.label}</span>
      </div>

      <div className="contact-row">
        {request.contact_phone && (
          <a className="contact-chip" href={`tel:${request.contact_phone}`}><Icon name="phone" size={15} />{request.contact_phone}</a>
        )}
        {request.contact_email && (
          <a className="contact-chip" href={`mailto:${request.contact_email}`}><Icon name="mail" size={15} />{request.contact_email}</a>
        )}
        <a className="contact-chip" href={mapUrl} target="_blank" rel="noreferrer" title="Открыть на карте">
          <Icon name="mapPin" size={15} />
          {request.address}
          {request.district ? <span className="muted">· {request.district}</span> : null}
        </a>
      </div>

      {!request.is_address_confirmed && (
        <Alert tone="warn">Адрес введён вручную, не из подсказки — район мог определиться неточно.</Alert>
      )}

      <h3 className="section-title" style={{ margin: "8px 4px 8px" }}>Что поверить</h3>
      <ul className="lines">
        {request.items.map((item) => (
          <li key={item.id}>
            <span className="grow">{item.family}</span>
            <span className="num muted">{item.quantity} × {money(item.unit_price)} ₽</span>
            <span className="num" style={{ minWidth: 80, textAlign: "right" }}>{money(item.subtotal)} ₽</span>
          </li>
        ))}
        {request.items.length === 0 && <li className="muted">Приборы не указаны</li>}
        {request.estimated_price && (
          <li className="total">
            <span className="grow">
              Итого{Number(request.discount_percent) > 0 ? ` со скидкой ${Number(request.discount_percent)} %` : ""}
            </span>
            <span className="num">≈ {money(request.estimated_price)} ₽</span>
          </li>
        )}
        {!request.estimated_price && request.items.length > 0 && (
          <li className="total"><span className="grow">Итого</span><span className="num">{money(total)} ₽</span></li>
        )}
      </ul>

      {(request.si_description || request.comment) && (
        <>
          <h3 className="section-title" style={{ margin: "20px 4px 8px" }}>Комментарий заявителя</h3>
          <div className="quote">{[request.si_description, request.comment].filter(Boolean).join("\n\n")}</div>
        </>
      )}

      <h3 className="section-title" style={{ margin: "20px 4px 8px" }}>Выезд</h3>
      <div className="kv" style={{ marginBottom: 0 }}>
        <div>
          <span className="k">Желаемое время</span>
          <span className="v">
            {request.desired_date ? `${formatDate(request.desired_date)}${request.desired_time ? `, ${request.desired_time.slice(0, 5)}` : ""}` : "не выбрано"}
            {request.is_priority_slot && <span className="pill ok" style={{ marginLeft: 8 }}>приоритетный слот</span>}
          </span>
        </div>
        {request.suggested_employee && (
          <div><span className="k">Рекомендован</span><span className="v">{request.suggested_employee}</span></div>
        )}
        {request.assigned_employee && (
          <div><span className="k">Исполнитель</span><span className="v">{request.assigned_employee}</span></div>
        )}
        {request.reject_reason && (
          <div><span className="k">Причина отказа</span><span className="v">{request.reject_reason}</span></div>
        )}
        <div><span className="k">Согласие на ПДн</span><span className="v">{request.consent_given ? "получено" : <span className="warn-text">нет</span>}</span></div>
      </div>

      {actionable && (
        <div className="confirm-box">
          <h4>Подтвердить и создать наряд</h4>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="req-emp">Исполнитель</label>
              <select id="req-emp" value={employee} onChange={(e) => setEmployee(e.target.value)}>
                <option value="">выберите</option>
                {employees.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.full_name}{e.id === request.suggested_employee_id ? " — рекомендован" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="req-date">Дата выезда</label>
              <input id="req-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="req-time">Время</label>
              <input id="req-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </div>
            <div className="field span-all">
              <label htmlFor="req-note">Примечание для поверителя <span className="opt">— необязательно</span></label>
              <input id="req-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Подъезд, код домофона, кто встретит…" />
            </div>
          </div>
          {error && <Alert>{error}</Alert>}
          <div className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="primary" disabled={busy} onClick={() => void confirm()}>
              <Icon name="check" size={16} /> {busy ? "Создаю…" : "Создать наряд"}
            </button>
            <span className="grow" />
            <button type="button" className="ghost" disabled={busy} onClick={() => void reject(false)}>Отклонить</button>
            <button type="button" className="ghost danger" disabled={busy} onClick={() => void reject(true)}>
              <Icon name="ban" size={15} /> Спам
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
