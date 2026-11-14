import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import {
  createOrder,
  downloadOrderAct,
  fetchProcurements,
  issueOrder,
  lookupParty,
  suggestParties,
  updateOrder,
  type OrderPayload,
  type PartySuggestion,
  type ProcurementRow,
  type WorkOrder,
} from "../api";
import Icon from "../ui/Icon";
import { Modal, confirmDialog } from "../ui/Modal";
import { Alert } from "../ui/controls";

/** Заказы во вкладке «Поверки»: заказчик привёз приборы сам.
 *
 * Заказ — тот же наряд, что и выезд, только без адреса и карты (kind = "lab"),
 * поэтому приборы в нём заводятся и поверяются тем же экраном. Здесь — то,
 * чего у выезда нет: заказчик (поиск по названию или ИНН), кто сдал, закупка, акты приёма и выдачи. */

const partyStatus = (status: string) =>
  ({ LIQUIDATING: "ликвидируется", LIQUIDATED: "ликвидирована", BANKRUPT: "банкрот", REORGANIZING: "реорганизуется" } as Record<string, string>)[status] ?? status.toLowerCase();

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "";

export const orderTitle = (o: WorkOrder) => (o.kind === "lab" ? o.client : o.site || "Адрес не указан");

/** Где заказ сейчас: принимаем → поверяем → готов к выдаче → выдан. */
export function orderStage(o: WorkOrder): { label: string; tone: string } {
  if (o.status === "cancelled") return { label: "Отменён", tone: "bad" };
  if (o.issued_at) return { label: `Выдан ${when(o.issued_at)}`, tone: "muted" };
  if (o.verifications_total === 0) return { label: "Приём", tone: "info" };
  if (o.verifications_ready < o.verifications_total) return { label: "Поверка", tone: "warn" };
  return { label: "Готов к выдаче", tone: "ok" };
}

export function OrderLine({ o, isManager }: { o: WorkOrder; isManager: boolean }) {
  const parts =
    o.kind === "lab"
      ? [
          o.client_inn && `ИНН ${o.client_inn}`,
          o.verifications_total ? `${o.verifications_total} шт.` : "",
          o.received_at && `принят ${when(o.received_at)}`,
          o.procurement_label && `закупка ${o.procurement_label.split(" · ")[0]}`,
          isManager ? o.assigned_employee : "",
        ]
      : [o.client, isManager ? o.assigned_employee : "", o.note];
  return <div className="sub">{parts.filter(Boolean).join(" · ")}</div>;
}

export function OrderHeader({
  order, onChanged, onError,
}: {
  order: WorkOrder;
  onChanged: (order: WorkOrder) => void;
  onError: (text: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState("");
  const stage = orderStage(order);

  const act = async (kind: "receive" | "issue") => {
    setBusy(kind);
    try {
      await downloadOrderAct(order.id, kind);
    } catch (exc) {
      onError(exc instanceof Error ? exc.message : "Не удалось сформировать акт");
    } finally {
      setBusy("");
    }
  };

  const toggleIssued = async () => {
    const issuing = !order.issued_at;
    if (issuing && order.verifications_ready < order.verifications_total) {
      const ok = await confirmDialog({
        title: "Выдать заказ?",
        text: `Поверено ${order.verifications_ready} из ${order.verifications_total}. Выдать всё равно?`,
        ok: "Выдать",
      });
      if (!ok) return;
    }
    setBusy("issue-toggle");
    try {
      onChanged(await issueOrder(order.id, issuing));
    } catch (exc) {
      onError(exc instanceof Error ? exc.message : "Не удалось отметить выдачу");
    } finally {
      setBusy("");
    }
  };

  return (
    <>
      <div className="detail-title">
        <div className="sub">Заказ № {order.id}{order.received_at ? ` · принят ${when(order.received_at)}` : ""}</div>
        <h2>{order.client}</h2>
      </div>
      <div className="trip-headline">
        <span className={`pill ${stage.tone}`}>{stage.label}</span>
        <span className="spacer" />
        <button type="button" className="icon-btn" aria-label="Изменить заказ" onClick={() => setEditing(true)}>
          <Icon name="pen" size={17} />
        </button>
      </div>
      <div className="kv">
        {order.client_inn && <div><span className="k">ИНН</span><span className="v">{order.client_inn}</span></div>}
        {order.client_address && <div><span className="k">Адрес</span><span className="v">{order.client_address}</span></div>}
        {order.contact && <div><span className="k">Кто сдал</span><span className="v">{order.contact}</span></div>}
        {order.client_phone && <div><span className="k">Телефон</span><span className="v">{order.client_phone}</span></div>}
        {order.procurement_label && <div><span className="k">Закупка</span><span className="v">{order.procurement_label}</span></div>}
        <div><span className="k">Принял</span><span className="v">{order.assigned_employee}</span></div>
        {order.note && <div><span className="k">Примечание</span><span className="v">{order.note}</span></div>}
      </div>
      <div className="order-actions">
        <button type="button" disabled={!!busy || order.verifications_total === 0} onClick={() => void act("receive")}>
          <Icon name="doc" size={16} /> {busy === "receive" ? "Формирую…" : "Акт приёма"}
        </button>
        <button type="button" disabled={!!busy || order.verifications_total === 0} onClick={() => void act("issue")}>
          <Icon name="doc" size={16} /> {busy === "issue" ? "Формирую…" : "Акт выдачи"}
        </button>
        <span className="spacer" />
        {order.status !== "cancelled" && (
          <button
            type="button"
            className={order.issued_at ? "" : "primary"}
            disabled={!!busy || order.verifications_total === 0}
            onClick={() => void toggleIssued()}
          >
            {order.issued_at ? <><Icon name="undo" size={16} /> Вернуть в работу</> : <><Icon name="check" size={16} /> Выдано заказчику</>}
          </button>
        )}
      </div>
      {order.verifications_total === 0 && (
        <p className="hint" style={{ margin: "0 4px 12px" }}>Добавьте приборы, которые привезли, — потом распечатайте акт приёма.</p>
      )}
      {editing && (
        <OrderModal
          order={order}
          onClose={() => setEditing(false)}
          onSaved={(fresh) => {
            setEditing(false);
            onChanged(fresh);
          }}
        />
      )}
    </>
  );
}

export function OrderModal({
  order, onClose, onSaved,
}: {
  order?: WorkOrder;
  onClose: () => void;
  onSaved: (order: WorkOrder) => void;
}) {
  const [inn, setInn] = useState(order?.client_inn ?? "");
  const [name, setName] = useState(order?.client ?? "");
  const [kpp, setKpp] = useState("");
  const [address, setAddress] = useState(order?.client_address ?? "");
  const [phone, setPhone] = useState(order?.client_phone ?? "");
  const [contact, setContact] = useState(order?.contact ?? "");
  const [note, setNote] = useState(order?.note ?? "");
  const [procurement, setProcurement] = useState<number | "">(order?.procurement_id ?? "");
  const [procurements, setProcurements] = useState<ProcurementRow[]>([]);
  const [lookup, setLookup] = useState<"" | "busy" | "found" | "own" | "none" | "picked">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const last = useRef(order?.client_inn ?? "");
  // Подсказки по названию: ищем, только когда человек сам печатает в поле.
  const [typed, setTyped] = useState(false);
  const [options, setOptions] = useState<PartySuggestion[]>([]);
  const [searching, setSearching] = useState(false);
  const [showList, setShowList] = useState(false);
  const [active, setActive] = useState(-1);
  const picked = useRef<PartySuggestion | null>(null);

  useEffect(() => {
    fetchProcurements({ state: "open" }).then(setProcurements).catch(() => undefined);
  }, []);

  // Примерное название (или начало ИНН) → список организаций: своя база, потом DaData.
  useEffect(() => {
    const query = name.trim();
    if (!typed || query.length < 2) {
      setOptions([]);
      setSearching(false);
      return;
    }
    const abort = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(() => {
      suggestParties(query, abort.signal)
        .then((found) => {
          setOptions(found);
          setActive(found.length ? 0 : -1);
          setShowList(true);
        })
        .catch(() => undefined)
        .finally(() => {
          if (!abort.signal.aborted) setSearching(false);
        });
    }, 300);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
  }, [name, typed]);

  const pick = (item: PartySuggestion) => {
    picked.current = item;
    last.current = item.inn; // чтобы поиск по ИНН не перетёр выбранное
    setTyped(false);
    setShowList(false);
    setOptions([]);
    setName(item.name);
    setInn(item.inn);
    setKpp(item.kpp);
    setAddress(item.address);
    if (item.phone && !phone) setPhone(item.phone);
    setLookup(item.source === "own" ? "own" : "picked");
  };

  const typeName = (value: string) => {
    setName(value);
    setTyped(true);
    // Начали менять выбранную организацию — её реквизиты больше не про неё.
    const prev = picked.current;
    if (prev && value.trim() !== prev.name) {
      picked.current = null;
      setInn((v) => (v === prev.inn ? "" : v));
      setKpp((v) => (v === prev.kpp ? "" : v));
      setAddress((v) => (v === prev.address ? "" : v));
      last.current = "";
      setLookup("");
    }
  };

  const onNameKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!showList || options.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i <= 0 ? options.length - 1 : i - 1));
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      pick(options[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setShowList(false);
    }
  };

  // ИНН набран целиком вручную — подтягиваем организацию: сначала из своей базы, потом из DaData.
  useEffect(() => {
    const digits = inn.replace(/\D/g, "");
    if ((digits.length !== 10 && digits.length !== 12) || digits === last.current) return;
    last.current = digits;
    setLookup("busy");
    lookupParty(digits)
      .then((found) => {
        if (!found.found) {
          setLookup("none");
          return;
        }
        setName(found.name);
        setKpp(found.kpp);
        if (found.address) setAddress(found.address);
        if (found.phone && !phone) setPhone(found.phone);
        setLookup(found.source === "own" ? "own" : "found");
      })
      .catch(() => setLookup("none"));
  }, [inn, phone]);

  const save = async () => {
    const digits = inn.replace(/\D/g, "");
    if (!digits && !name.trim()) {
      setError("Укажите ИНН или название заказчика");
      return;
    }
    setBusy(true);
    setError("");
    const payload: OrderPayload = {
      inn: digits, name: name.trim(), kpp, address: address.trim(), phone: phone.trim(),
      contact: contact.trim(), note, procurement_id: procurement ? Number(procurement) : null,
    };
    try {
      onSaved(order ? await updateOrder(order.id, payload) : await createOrder(payload));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить заказ");
      setBusy(false);
    }
  };

  const hint = {
    "": "Начните вводить название или ИНН — выберите организацию из списка",
    busy: "Ищу организацию по ИНН…",
    found: "Нашёл по ИНН — проверьте и при необходимости поправьте",
    picked: "Реквизиты подставлены — проверьте и при необходимости поправьте",
    own: "Заказчик уже сдавал приборы — данные из нашей базы",
    none: "По ИНН ничего не нашёл — впишите название и адрес вручную",
  }[lookup];
  const nothingFound = typed && !searching && showList && options.length === 0 && name.trim().length >= 2;

  return (
    <Modal
      open
      onClose={onClose}
      title={order ? "Заказ" : "Приём приборов"}
      subtitle={order ? order.client : "Новый заказ — заказчик привёз приборы"}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Сохраняю…" : order ? "Сохранить" : "Принять"}
          </button>
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
        <div className="field suggest-field">
          <label htmlFor="order-name">Заказчик</label>
          <input
            id="order-name"
            value={name}
            onChange={(event) => typeName(event.target.value)}
            onKeyDown={onNameKey}
            onFocus={() => setShowList(options.length > 0)}
            onBlur={() => window.setTimeout(() => setShowList(false), 150)}
            autoComplete="off"
            autoFocus={!order}
            placeholder="Название или ИНН — например, Водоканал"
            role="combobox"
            aria-expanded={showList}
            aria-controls="order-name-list"
          />
          {showList && options.length > 0 && (
            <ul className="suggestions" id="order-name-list" role="listbox">
              {options.map((item, index) => (
                <li
                  key={`${item.source}-${item.inn || item.client_id || item.name}`}
                  role="option"
                  aria-selected={index === active}
                  className={index === active ? "active" : ""}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    pick(item);
                  }}
                  style={{ flexDirection: "column", gap: 2 }}
                >
                  <span>
                    {item.name}
                    {item.source === "own" && <span className="sub"> · уже сдавал</span>}
                    {item.status && item.status !== "ACTIVE" && (
                      <span className="sub"> · {partyStatus(item.status)}</span>
                    )}
                  </span>
                  <span className="sub" style={{ whiteSpace: "normal", fontSize: 12, opacity: 0.8 }}>
                    {[item.inn && `ИНН ${item.inn}`, item.address].filter(Boolean).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div className={`hint ${lookup === "found" || lookup === "own" || lookup === "picked" ? "ok" : ""}`}>
            {searching ? "Ищу организации…" : nothingFound ? "Ничего не нашёл — впишите название и ИНН вручную" : hint}
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="order-inn">ИНН</label>
            <input
              id="order-inn"
              value={inn}
              onChange={(event) => {
                picked.current = null;
                setInn(event.target.value.replace(/[^\d]/g, "").slice(0, 12));
              }}
              inputMode="numeric"
              autoComplete="off"
              placeholder="подставится из списка"
            />
          </div>
          <div className="field" style={{ flex: "0 0 140px" }}>
            <label htmlFor="order-kpp">КПП</label>
            <input
              id="order-kpp"
              value={kpp}
              onChange={(event) => setKpp(event.target.value.replace(/[^\d]/g, "").slice(0, 9))}
              inputMode="numeric"
              autoComplete="off"
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="order-address">Адрес <span className="opt">— юридический, пойдёт в журнал</span></label>
          <input id="order-address" value={address} onChange={(event) => setAddress(event.target.value)} />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="order-contact">Кто сдал</label>
            <input id="order-contact" value={contact} onChange={(event) => setContact(event.target.value)} placeholder="Петров И. И., механик" />
          </div>
          <div className="field">
            <label htmlFor="order-phone">Телефон</label>
            <input id="order-phone" type="tel" inputMode="tel" value={phone} onChange={(event) => setPhone(event.target.value)} />
          </div>
        </div>
        {procurements.length > 0 && (
          <div className="field">
            <label htmlFor="order-procurement">По закупке <span className="opt">— если в счёт контракта</span></label>
            <select id="order-procurement" value={procurement} onChange={(event) => setProcurement(event.target.value ? Number(event.target.value) : "")}>
              <option value="">— нет —</option>
              {procurements.map((item) => (
                <option key={item.id} value={item.id}>{item.customer} · {item.label}</option>
              ))}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="order-note">Примечание</label>
          <textarea id="order-note" rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Срочно к пятнице, без упаковки…" />
        </div>
        {error && <Alert>{error}</Alert>}
      </form>
    </Modal>
  );
}
