/** «Новая приёмка» за стойкой: заказчик → кто сдал → приборы таблицей → сохранить → акт приёма.
 *
 * Один лист, без шагов-экранов: выбрали заказчика — фокус сам уходит дальше. Заказчик —
 * недавние сверху, поиск по названию или ИНН (своя база → DaData), физлицо одним касанием.
 * Приёмка заводится при первом сохранении приборов, приборы — пачкой (bulk, идемпотентно). */

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  createOrder,
  downloadOrderAct,
  fetchEmployees,
  fetchProcurements,
  lookupParty,
  suggestParties,
  type Employee,
  type Me,
  type OrderPayload,
  type PartySuggestion,
  type ProcurementRow,
  type WorkOrder,
} from "../../api";
import { errorText } from "../../components/tripUtil";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { Sheet } from "../../ui/Sheet";
import toast from "../../ui/Toast";
import { bulkInstruments, type BulkResult, type BulkRow } from "./api";
import FastTable from "./FastTable";

export type Customer = { client_id?: number | null; inn: string; name: string; kpp?: string; address: string; phone?: string; source?: string };
export const PERSON: Customer = { inn: "", name: "Физическое лицо", address: "" };

export default function NewIntake({
  me, isManager, recent, onClose, onCreated, onOpen,
}: {
  me: Me | null;
  isManager: boolean;
  /** Недавние заказчики (из последних приёмок) — сверху, одним касанием. */
  recent: Customer[];
  onClose: () => void;
  /** Приёмка заведена или пополнена — обновить доску. */
  onCreated: (order: WorkOrder) => void;
  onOpen: (id: number) => void;
}) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<PartySuggestion[]>([]);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(0);
  const [contact, setContact] = useState("");
  const [phone, setPhone] = useState("");
  const [note, setNote] = useState("");
  const [procurement, setProcurement] = useState<number | "">("");
  const [procurements, setProcurements] = useState<ProcurementRow[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [assignee, setAssignee] = useState<number | "">(me?.employee?.id ?? "");
  const [order, setOrder] = useState<WorkOrder | null>(null);
  const [saved, setSaved] = useState(0);
  const [printing, setPrinting] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const contactRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchProcurements({ state: "open" }).then(setProcurements).catch(() => undefined);
    if (isManager) fetchEmployees().then(setEmployees).catch(() => undefined);
  }, [isManager]);

  useEffect(() => {
    const t = window.setTimeout(() => searchRef.current?.focus(), 60);
    return () => window.clearTimeout(t);
  }, []);

  // Поиск: ИНН целиком — точный lookup, иначе подсказки по названию.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setOptions([]);
      return;
    }
    const abort = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const digits = q.replace(/\D/g, "");
        if ((digits.length === 10 || digits.length === 12) && digits === q.replace(/\s/g, "")) {
          const found = await lookupParty(digits);
          if (abort.signal.aborted) return;
          setOptions(found.found ? [{ ...found, client_id: found.client_id }] as PartySuggestion[] : []);
        } else {
          const found = await suggestParties(q, abort.signal);
          if (!abort.signal.aborted) setOptions(found);
        }
        setActive(0);
      } catch {
        /* нет связи — молчим, можно вписать вручную */
      } finally {
        if (!abort.signal.aborted) setSearching(false);
      }
    }, 280);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
  }, [query]);

  const choose = (value: Customer) => {
    setCustomer(value);
    if (value.phone && !phone) setPhone(value.phone);
    setOptions([]);
    setQuery("");
    window.setTimeout(() => contactRef.current?.focus(), 30);
  };

  const manual = () => {
    const q = query.trim();
    const digits = q.replace(/\D/g, "");
    choose(digits.length === 10 || digits.length === 12 ? { inn: digits, name: "", address: "" } : { inn: "", name: q, address: "" });
  };

  const onSearchKey = (event: KeyboardEvent<HTMLInputElement>) => {
    const total = options.length;
    if (event.key === "ArrowDown" && total) {
      event.preventDefault();
      setActive((i) => (i + 1) % total);
    } else if (event.key === "ArrowUp" && total) {
      event.preventDefault();
      setActive((i) => (i <= 0 ? total - 1 : i - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (options[active]) choose(options[active]);
      else if (query.trim().length >= 2) manual();
    }
  };

  const ensureOrder = async (): Promise<WorkOrder> => {
    if (order) return order;
    if (!customer) throw new Error("Выберите заказчика");
    if (!customer.inn && !customer.name.trim()) throw new Error("Укажите название или ИНН заказчика");
    const payload: OrderPayload = {
      inn: customer.inn, name: customer.name.trim(), kpp: customer.kpp ?? "", address: customer.address.trim(),
      phone: phone.trim(), contact: contact.trim(), note, procurement_id: procurement ? Number(procurement) : null,
      ...(isManager && assignee ? { assigned_employee_id: Number(assignee) } : {}),
    };
    const created = await createOrder(payload);
    setOrder(created);
    return created;
  };

  const save = async (rows: BulkRow[]): Promise<BulkResult | null> => {
    try {
      const target = await ensureOrder();
      const result = await bulkInstruments(target.id, rows);
      setSaved((n) => n + result.created.length);
      const fresh = {
        ...target,
        verifications_total: target.verifications_total + result.created.length,
      };
      setOrder(fresh);
      onCreated(fresh);
      if (result.errors.length) {
        toast.warn(`Сохранено ${result.created.length}, с ошибкой ${result.errors.length}`, { description: result.errors[0].error });
      } else {
        toast.success(`Принято приборов: ${result.created.length}${result.existing.length ? ` (уже были: ${result.existing.length})` : ""}`, {
          description: `${target.client} · приёмка № ${target.id}`,
        });
      }
      return result;
    } catch (exc) {
      toast.error("Не сохранилось", { description: errorText(exc, "Нет связи — повторите, строки не потеряются") });
      return null;
    }
  };

  const printAct = async () => {
    if (!order) return;
    setPrinting(true);
    try {
      await downloadOrderAct(order.id, "receive");
    } catch (exc) {
      toast.error("Акт не сформировался", { description: errorText(exc, "") });
    } finally {
      setPrinting(false);
    }
  };

  const recentShown = recent.slice(0, 6);

  return (
    <Sheet
      open
      onClose={onClose}
      size="lg"
      title={order ? `Приёмка № ${order.id}` : "Новая приёмка"}
      subtitle={order ? `${order.client} · принято ${saved}` : "Заказчик принёс приборы"}
      footer={
        <>
          <button type="button" onClick={onClose}>{order ? "Готово" : "Отмена"}</button>
          <span className="spacer" />
          {order && (
            <>
              <button type="button" onClick={() => onOpen(order.id)}><Icon name="arrowUpRight" size={15} /> Открыть приёмку</button>
              <button type="button" className="primary" disabled={printing || saved === 0} onClick={() => void printAct()}>
                <Icon name="printer" size={15} /> {printing ? "Формирую…" : "Акт приёма"}
              </button>
            </>
          )}
        </>
      }
    >
      <div className="it-new">
        <section className="it-step">
          <div className="it-step-title"><span className="it-step-n">1</span> Заказчик</div>
          {customer ? (
            <div className="it-customer card">
              <div className="grow">
                {order ? <b>{customer.name || order.client}</b> : (
                  <input className="it-customer-name" value={customer.name} placeholder="Название организации"
                         onChange={(e) => setCustomer({ ...customer, name: e.target.value })} aria-label="Название" />
                )}
                <div className="it-customer-fields">
                  <label>ИНН <input className="data" value={customer.inn} disabled={!!order} inputMode="numeric"
                                     onChange={(e) => setCustomer({ ...customer, inn: e.target.value.replace(/\D/g, "").slice(0, 12) })} /></label>
                  <label className="grow">Адрес <input value={customer.address} disabled={!!order}
                                     onChange={(e) => setCustomer({ ...customer, address: e.target.value })} placeholder="юридический — пойдёт в журнал" /></label>
                </div>
                {customer.source === "own" && <div className="sub pass-text">уже сдавал приборы — данные из нашей базы</div>}
              </div>
              {!order && <button type="button" className="ghost sm" onClick={() => { setCustomer(null); window.setTimeout(() => searchRef.current?.focus(), 30); }}>Другой</button>}
            </div>
          ) : (
            <>
              <div className="it-quick">
                <button type="button" className="chip" onClick={() => choose(PERSON)}><Icon name="user" size={14} /> Физлицо</button>
                {recentShown.map((c) => (
                  <button type="button" key={`${c.client_id ?? c.inn}-${c.name}`} className="chip" onClick={() => choose({ ...c, source: "own" })} title={c.inn ? `ИНН ${c.inn}` : undefined}>
                    <Icon name="history" size={13} /> {c.name}
                  </button>
                ))}
              </div>
              <div className="suggest-field it-search">
                <input
                  ref={searchRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={onSearchKey}
                  placeholder="Название или ИНН — Водоканал, 4345…"
                  autoComplete="off"
                  role="combobox"
                  aria-expanded={options.length > 0}
                  aria-label="Заказчик"
                />
                {options.length > 0 && (
                  <ul className="suggestions" role="listbox">
                    {options.map((item, index) => (
                      <li key={`${item.source}-${item.inn || item.client_id || item.name}`} role="option" aria-selected={index === active}
                          className={index === active ? "active" : ""} onMouseEnter={() => setActive(index)}
                          onMouseDown={(e) => { e.preventDefault(); choose(item); }} style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                        <span>{item.name}{item.source === "own" && <span className="sub"> · уже сдавал</span>}</span>
                        <span className="sub" style={{ fontSize: 12 }}>{[item.inn && `ИНН ${item.inn}`, item.address].filter(Boolean).join(" · ")}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="it-hint">
                  {searching ? "Ищу…" : query.trim().length >= 2 && options.length === 0
                    ? <>Не нашёл — <button type="button" className="link" onClick={manual}>вписать вручную</button> (<Kbd>Enter</Kbd>)</>
                    : <>Стрелки и <Kbd>Enter</Kbd> — выбрать</>}
                </div>
              </div>
            </>
          )}
        </section>

        <section className={`it-step ${customer ? "" : "is-muted"}`}>
          <div className="it-step-title"><span className="it-step-n">2</span> Кто сдал</div>
          <div className="field-row">
            <div className="field">
              <input ref={contactRef} value={contact} disabled={!!order} onChange={(e) => setContact(e.target.value)} placeholder="Петров И. И., механик" aria-label="Кто сдал"
                     onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (document.querySelector<HTMLInputElement>('.it-table input[data-row="0"][data-col="type"]') ?? document.querySelector<HTMLInputElement>('.it-table [data-row="0"][data-col="serial"]'))?.focus(); } }} />
            </div>
            <div className="field" style={{ flex: "0 0 180px" }}>
              <input type="tel" inputMode="tel" value={phone} disabled={!!order} onChange={(e) => setPhone(e.target.value)} placeholder="Телефон" aria-label="Телефон" />
            </div>
          </div>
          <div className="field-row">
            {procurements.length > 0 && (
              <div className="field">
                <select value={procurement} disabled={!!order} onChange={(e) => setProcurement(e.target.value ? Number(e.target.value) : "")} aria-label="Закупка">
                  <option value="">Не по закупке</option>
                  {procurements.map((p) => <option key={p.id} value={p.id}>{p.label} · {p.customer}</option>)}
                </select>
              </div>
            )}
            {isManager && employees.length > 0 && (
              <div className="field">
                <select value={assignee} disabled={!!order} onChange={(e) => setAssignee(e.target.value ? Number(e.target.value) : "")} aria-label="Ответственный">
                  {!me?.employee && <option value="">Ответственный…</option>}
                  {employees.map((e) => <option key={e.id} value={e.id}>Ответственный: {e.full_name}</option>)}
                </select>
              </div>
            )}
          </div>
          <input value={note} disabled={!!order} onChange={(e) => setNote(e.target.value)} placeholder="Примечание: срочно к пятнице, без упаковки…" aria-label="Примечание" />
        </section>

        <section className={`it-step ${customer ? "" : "is-muted"}`}>
          <div className="it-step-title"><span className="it-step-n">3</span> Приборы</div>
          <FastTable onSave={save} saveLabel={order ? "Добавить" : "Принять"} autoFocus={false} />
        </section>
      </div>
    </Sheet>
  );
}
