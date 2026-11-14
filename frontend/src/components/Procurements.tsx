import { useCallback, useEffect, useRef, useState } from "react";
import {
  createPass,
  createProcurement,
  deletePass,
  deleteProcurementDocument,
  downloadProcurementDocument,
  fetchEmployees,
  fetchProcurement,
  updatePass,
  updateProcurement,
  uploadProcurementDocument,
  type Employee,
  type Procurement,
  type ProcurementDocument,
  type ProcurementItem,
  type ProcurementPass,
} from "../api";
import Icon from "../ui/Icon";
import { Modal, confirmDialog } from "../ui/Modal";
import { Alert, EmptyState, Spinner } from "../ui/controls";
import { DeadlinePill } from "./TripBits";
import { TRIP_TONE, dateRange, errorText, instruments, ruDate, ruShort } from "./tripUtil";

/** Закупка: реквизиты контракта, объём, заходы, документы и пропуска.
 *
 * Контракт живёт дольше одной поездки, поэтому всё, что относится к нему, а не
 * к конкретному выезду, лежит здесь: документы и пропуска действуют во всех
 * заходах, а «поверено N из M» считается по всем командировкам закупки. */

const LAWS = [
  { value: "44", label: "44-ФЗ" },
  { value: "223", label: "223-ФЗ" },
  { value: "direct", label: "Прямой договор" },
  { value: "other", label: "Другое" },
];

const STATUSES = [
  { value: "preparing", label: "Подготовка" },
  { value: "active", label: "Исполняется" },
  { value: "done", label: "Исполнен" },
  { value: "cancelled", label: "Расторгнут" },
];

const DOC_KINDS = [
  { value: "contract", label: "Контракт" },
  { value: "notice", label: "Извещение / ТЗ" },
  { value: "pass_list", label: "Заявка на пропуска" },
  { value: "act", label: "Акт" },
  { value: "invoice", label: "Счёт" },
  { value: "letter", label: "Письмо" },
  { value: "other", label: "Другое" },
];

const PASS_STATUSES = [
  { value: "needed", label: "Нужно заказать" },
  { value: "requested", label: "Заявка подана" },
  { value: "issued", label: "Оформлен" },
  { value: "refused", label: "Отказано" },
];

const PASS_TONE: Record<ProcurementPass["status"], string> = { needed: "bad", requested: "warn", issued: "ok", refused: "bad" };

const money = (value: string | null) =>
  value ? `${Number(value).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽` : "";

const fileSize = (bytes: number) =>
  bytes > 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} МБ` : bytes > 0 ? `${Math.max(1, Math.round(bytes / 1024))} КБ` : "";

export default function ProcurementView({
  procurementId, onBack, onOpenTrip, onNewVisit,
}: {
  procurementId: number;
  onBack: () => void;
  onOpenTrip: (id: number) => void;
  onNewVisit: (procurementId: number) => void;
}) {
  const [item, setItem] = useState<Procurement | null>(null);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [passEdit, setPassEdit] = useState<ProcurementPass | "new" | null>(null);
  const [docKind, setDocKind] = useState("contract");
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    try {
      setItem(await fetchProcurement(procurementId));
    } catch (exc) {
      if ((exc as { status?: number }).status === 404) setMissing(true);
      else setError(errorText(exc, "Не удалось загрузить закупку"));
    }
  }, [procurementId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const back = (
    <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Закупки</button>
  );
  if (missing) {
    return (
      <div className="narrow">
        {back}
        <EmptyState icon="briefcase" title="Закупка не найдена">Возможно, вы не участвуете в командировках по ней.</EmptyState>
      </div>
    );
  }
  if (!item) return <div className="narrow">{back}{error ? <Alert>{error}</Alert> : <Spinner />}</div>;

  const closed = item.status === "done" || item.status === "cancelled";
  const { planned, ready, entered, unsuitable } = item.progress;
  const share = planned > 0 ? Math.min(100, (ready / planned) * 100) : 0;

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    setUploading(true);
    setError("");
    try {
      for (const file of files) await uploadProcurementDocument(item.id, file, docKind);
      await reload();
    } catch (exc) {
      setError(errorText(exc, "Не удалось загрузить файл"));
    } finally {
      setUploading(false);
    }
  };

  const removeDocument = async (doc: ProcurementDocument) => {
    if (!(await confirmDialog({ title: "Удалить документ?", text: doc.title, ok: "Удалить", danger: true }))) return;
    try {
      await deleteProcurementDocument(doc.id);
      await reload();
    } catch (exc) {
      setError(errorText(exc, "Не удалось удалить"));
    }
  };

  const download = (doc: ProcurementDocument) =>
    downloadProcurementDocument(doc).catch((exc) => setError(errorText(exc, "Не удалось скачать файл")));

  return (
    <div className="narrow">
      {back}
      <div className="detail-title">
        <div className="sub">{[item.label, item.law_display].join(" · ")}</div>
        <h2>{item.customer}</h2>
      </div>
      <div className="trip-headline">
        <span className={`pill ${item.status === "active" ? "ok" : item.status === "cancelled" ? "bad" : "muted"}`}>{item.status_display}</span>
        <DeadlinePill deadline={item.deadline} closed={closed} />
        <span className="spacer" />
        {item.open_url && (
          <a className="btn sm" href={item.open_url} target="_blank" rel="noreferrer"><Icon name="external" size={14} /> Открыть закупку</a>
        )}
        {item.can_edit && (
          <button type="button" className="icon-btn" aria-label="Изменить закупку" onClick={() => setEditing(true)}>
            <Icon name="pen" size={17} />
          </button>
        )}
      </div>

      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      <div className="kv">
        <div><span className="k">Предмет</span><span className="v">{item.title}</span></div>
        <div>
          <span className="k">Заказчик</span>
          <span className="v">{item.customer}{item.customer_inn ? ` · ИНН ${item.customer_inn}` : ""}</span>
        </div>
        {(item.contract_number || item.contract_date) && (
          <div>
            <span className="k">Контракт</span>
            <span className="v">
              {[item.contract_number && `№ ${item.contract_number}`, item.contract_date && `от ${ruShort(item.contract_date)}`].filter(Boolean).join(" ")}
            </span>
          </div>
        )}
        {(item.date_start || item.deadline) && (
          <div><span className="k">Срок</span><span className="v">{dateRange(item.date_start, item.deadline)}{item.deadline ? ` ${item.deadline.slice(0, 4)}` : ""}</span></div>
        )}
        {item.amount && <div><span className="k">Цена контракта</span><span className="v">{money(item.amount)}</span></div>}
        {item.contact && <div><span className="k">Контакт у заказчика</span><span className="v">{item.contact}</span></div>}
        {item.responsible && <div><span className="k">Ответственный</span><span className="v">{item.responsible.full_name}</span></div>}
        {item.note && <div><span className="k">Примечание</span><span className="v">{item.note}</span></div>}
      </div>

      <h2>Объём</h2>
      <div className="card">
        <div className="trip-progress">
          <b>{ready}{planned > 0 && <small> из {planned}</small>}</b>
          <span>
            приборов поверено за все заходы
            {entered > ready ? ` · без измерений ещё ${entered - ready}` : ""}
            {unsuitable > 0 ? ` · непригодных ${unsuitable}` : ""}
          </span>
        </div>
        {planned > 0 && <div className="trip-bar"><i style={{ width: `${share}%` }} /></div>}
        {item.items.length > 0 ? (
          <ul className="item-list" style={{ marginTop: 12 }}>
            {item.items.map((row) => (
              <li key={row.id ?? row.title}><span className="name">{row.title}</span><span className="price">{row.quantity}</span></li>
            ))}
          </ul>
        ) : (
          item.can_edit && <div className="hint">Объём по контракту не указан — добавьте строки в карточке закупки, и здесь появится план/факт.</div>
        )}
      </div>

      <h2>Заходы <span className="count">{item.trips.length}</span></h2>
      {item.trips.length === 0 && <div className="empty small" style={{ textAlign: "left", padding: "0 4px 12px" }}>Командировок по закупке ещё не было</div>}
      <div className="card-list">
        {item.trips.map((trip, index) => (
          <button type="button" className="card tap" key={trip.id} onClick={() => onOpenTrip(trip.id)}>
            <div className="order-row">
              <div className="grow">
                <b>Заход {index + 1}</b>
                <div className="sub">{[dateRange(trip.date_start, trip.date_end), trip.lead.full_name].join(" · ")}</div>
                <div className="trip-tags"><span className={`pill ${TRIP_TONE[trip.status]}`}>{trip.status_display}</span></div>
              </div>
              <div className="order-progress">
                {trip.stops_total > 0 ? `${trip.stops_done} из ${trip.stops_total}` : "нет адресов"}
                {trip.instruments_entered > 0 && <small>{instruments(trip.instruments_entered)}</small>}
              </div>
            </div>
          </button>
        ))}
      </div>
      {!closed && (
        <button type="button" className="primary big wide" onClick={() => onNewVisit(item.id)}>
          <Icon name="plus" /> Новый заход
        </button>
      )}

      <h2>Документы <span className="count">{item.documents.length}</span></h2>
      {item.documents.length > 0 && (
        <ul className="item-list card-like">
          {item.documents.map((doc) => (
            <li key={doc.id}>
              <Icon name="file" size={17} />
              <span className="name">
                {doc.title}
                <div className="sub">{[doc.kind_display, fileSize(doc.size), ruShort(doc.created_at.slice(0, 10))].filter(Boolean).join(" · ")}</div>
              </span>
              <button type="button" className="sm" onClick={() => void download(doc)}><Icon name="download" size={14} /> Скачать</button>
              {item.can_edit && (
                <button type="button" className="icon-btn sm danger" aria-label="Удалить документ" onClick={() => void removeDocument(doc)}>
                  <Icon name="trash" size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {item.documents.length === 0 && !item.can_edit && <div className="empty small" style={{ textAlign: "left", padding: "0 4px 12px" }}>Документов нет</div>}
      {item.can_edit && (
        <div className="trip-upload">
          <select value={docKind} onChange={(event) => setDocKind(event.target.value)} aria-label="Вид документа">
            {DOC_KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
          </select>
          <button type="button" disabled={uploading} onClick={() => fileInput.current?.click()}>
            <Icon name="upload" size={16} /> {uploading ? "Загружаю…" : "Загрузить файл"}
          </button>
          <input ref={fileInput} type="file" multiple style={{ display: "none" }} onChange={(event) => void upload(event)} />
        </div>
      )}

      <h2>Пропуска <span className="count">{item.passes.length}</span></h2>
      {item.passes.length === 0 && (
        <div className="empty small" style={{ textAlign: "left", padding: "0 4px 12px" }}>
          Пропусков нет. Если на объекты заказчика пускают по пропускам — заведите их, и в командировке будет видно, у кого он есть на даты поездки.
        </div>
      )}
      {item.passes.length > 0 && (
        <ul className="item-list card-like">
          {item.passes.map((pass) => (
            <li key={pass.id}>
              <Icon name={pass.employee ? "user" : "car"} size={17} />
              <span className="name">
                {pass.holder}
                <div className="sub">
                  {[
                    pass.number && `№ ${pass.number}`,
                    pass.valid_from || pass.valid_to ? `${pass.valid_from ? `с ${ruShort(pass.valid_from)}` : ""} ${pass.valid_to ? `по ${ruShort(pass.valid_to)}` : ""}`.trim() : "",
                    pass.site,
                    pass.note,
                  ].filter(Boolean).join(" · ")}
                </div>
              </span>
              <span className={`pill ${PASS_TONE[pass.status]}`}>{pass.status_display}</span>
              {item.can_edit && (
                <button type="button" className="icon-btn sm" aria-label="Изменить пропуск" onClick={() => setPassEdit(pass)}>
                  <Icon name="pen" size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {item.can_edit && (
        <button type="button" className="wide" onClick={() => setPassEdit("new")}><Icon name="plus" size={16} /> Пропуск</button>
      )}

      {item.sites.length > 0 && (
        <>
          <h2>Объекты заказчика <span className="count">{item.sites.length}</span></h2>
          <ul className="item-list card-like">
            {item.sites.map((site) => (
              <li key={site.id}>
                <Icon name="mapPin" size={17} />
                <span className="name">{site.address}</span>
                {site.is_restricted && <span className="pill warn plain">режимный</span>}
              </li>
            ))}
          </ul>
        </>
      )}

      {editing && (
        <ProcurementForm
          item={item}
          onClose={() => setEditing(false)}
          onSaved={(fresh) => {
            setEditing(false);
            setItem(fresh);
          }}
        />
      )}
      {passEdit && (
        <PassModal
          procurement={item}
          pass={passEdit === "new" ? null : passEdit}
          onClose={() => setPassEdit(null)}
          onSaved={() => {
            setPassEdit(null);
            void reload();
          }}
        />
      )}
    </div>
  );
}

export function ProcurementForm({
  item, onClose, onSaved,
}: {
  item?: Procurement;
  onClose: () => void;
  onSaved: (item: Procurement) => void;
}) {
  const [number, setNumber] = useState(item?.number ?? "");
  const [law, setLaw] = useState(item?.law ?? "44");
  const [title, setTitle] = useState(item?.title ?? "");
  const [customer, setCustomer] = useState(item?.customer ?? "");
  const [inn, setInn] = useState(item?.customer_inn ?? "");
  const [contractNumber, setContractNumber] = useState(item?.contract_number ?? "");
  const [contractDate, setContractDate] = useState(item?.contract_date ?? "");
  const [dateStart, setDateStart] = useState(item?.date_start ?? "");
  const [deadline, setDeadline] = useState(item?.deadline ?? "");
  const [amount, setAmount] = useState(item?.amount ?? "");
  const [url, setUrl] = useState(item?.eis_url ?? "");
  const [status, setStatus] = useState(item?.status ?? "active");
  const [contact, setContact] = useState(item?.contact ?? "");
  const [note, setNote] = useState(item?.note ?? "");
  const [items, setItems] = useState<ProcurementItem[]>(item?.items.length ? item.items : [{ title: "", quantity: 1 }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const setRow = (index: number, patch: Partial<ProcurementItem>) =>
    setItems((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = async () => {
    if (!title.trim()) return setError("Укажите предмет закупки");
    if (!customer.trim()) return setError("Укажите заказчика");
    if (!number.trim() && !contractNumber.trim()) return setError("Нужен номер закупки или номер контракта");
    setBusy(true);
    setError("");
    const payload = {
      number: number.trim(),
      law,
      title: title.trim(),
      customer_name: customer.trim(),
      customer_inn: inn.trim(),
      contract_number: contractNumber.trim(),
      contract_date: contractDate || null,
      date_start: dateStart || null,
      deadline: deadline || null,
      amount: amount ? String(amount).replace(",", ".").replace(/\s/g, "") : null,
      eis_url: url.trim(),
      status,
      contact: contact.trim(),
      note,
      items: items
        .filter((row) => row.title.trim())
        .map((row) => ({ title: row.title.trim(), quantity: Math.max(1, Number(row.quantity) || 1) })),
    };
    try {
      onSaved(item ? await updateProcurement(item.id, payload) : await createProcurement(payload));
    } catch (exc) {
      setError(errorText(exc, "Не удалось сохранить закупку"));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={item ? "Закупка" : "Новая закупка"}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняю…" : item ? "Сохранить" : "Создать"}</button>
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
          <div className="field" style={{ flex: 2 }}>
            <label htmlFor="pr-number">Номер закупки</label>
            <input id="pr-number" value={number} onChange={(event) => setNumber(event.target.value)} placeholder="Номер извещения в ЕИС" inputMode="numeric" autoFocus={!item} />
          </div>
          <div className="field">
            <label htmlFor="pr-law">Основание</label>
            <select id="pr-law" value={law} onChange={(event) => setLaw(event.target.value)}>
              {LAWS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="pr-title">Предмет</label>
          <input id="pr-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Оказание услуг по поверке счётчиков воды" />
        </div>
        <div className="field-row">
          <div className="field" style={{ flex: 2 }}>
            <label htmlFor="pr-customer">Заказчик</label>
            <input id="pr-customer" value={customer} onChange={(event) => setCustomer(event.target.value)} placeholder="МУП «Водоканал»" />
          </div>
          <div className="field">
            <label htmlFor="pr-inn">ИНН <span className="opt">— необязательно</span></label>
            <input id="pr-inn" value={inn} onChange={(event) => setInn(event.target.value.replace(/\D/g, "").slice(0, 12))} inputMode="numeric" />
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="pr-contract">Номер контракта</label>
            <input id="pr-contract" value={contractNumber} onChange={(event) => setContractNumber(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="pr-contract-date">Дата контракта</label>
            <input id="pr-contract-date" type="date" value={contractDate} onChange={(event) => setContractDate(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="pr-amount">Цена, ₽</label>
            <input id="pr-amount" value={amount} onChange={(event) => setAmount(event.target.value)} inputMode="decimal" />
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="pr-start">Начало работ</label>
            <input id="pr-start" type="date" value={dateStart} onChange={(event) => setDateStart(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="pr-deadline">Срок исполнения</label>
            <input id="pr-deadline" type="date" value={deadline} min={dateStart} onChange={(event) => setDeadline(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="pr-status">Состояние</label>
            <select id="pr-status" value={status} onChange={(event) => setStatus(event.target.value)}>
              {STATUSES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
        </div>

        <div className="field">
          <span className="field-label">Объём по контракту — что и сколько поверить</span>
          <div className="trip-items">
            {items.map((row, index) => (
              <div className="trip-item-row" key={index}>
                <input value={row.title} onChange={(event) => setRow(index, { title: event.target.value })} placeholder="Счётчики воды Ду 15" aria-label="Что поверить" />
                <input
                  value={row.quantity}
                  onChange={(event) => setRow(index, { quantity: Number(event.target.value.replace(/\D/g, "")) || 0 })}
                  inputMode="numeric"
                  aria-label="Количество"
                />
                <button type="button" className="icon-btn" aria-label="Убрать строку" onClick={() => setItems((current) => current.filter((_, i) => i !== index))}>
                  <Icon name="x" size={15} />
                </button>
              </div>
            ))}
            <button type="button" className="sm" onClick={() => setItems((current) => [...current, { title: "", quantity: 1 }])}>
              <Icon name="plus" size={14} /> Строка
            </button>
          </div>
        </div>

        <div className="field">
          <label htmlFor="pr-contact">Контакт у заказчика <span className="opt">— кто встречает и подписывает акты</span></label>
          <input id="pr-contact" value={contact} onChange={(event) => setContact(event.target.value)} placeholder="Главный инженер Петров И. И., +7 …" />
        </div>
        <div className="field">
          <label htmlFor="pr-url">Ссылка на закупку <span className="opt">— если пусто, откроем поиск в ЕИС по номеру</span></label>
          <input id="pr-url" type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://zakupki.gov.ru/…" />
        </div>
        <div className="field">
          <label htmlFor="pr-note">Примечание</label>
          <textarea id="pr-note" rows={2} value={note} onChange={(event) => setNote(event.target.value)} />
        </div>
        {error && <Alert>{error}</Alert>}
      </form>
    </Modal>
  );
}

function PassModal({
  procurement, pass, onClose, onSaved,
}: {
  procurement: Procurement;
  pass: ProcurementPass | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [forWhat, setForWhat] = useState<"person" | "car">(pass && !pass.employee ? "car" : "person");
  const [employeeId, setEmployeeId] = useState<number | "">(pass?.employee?.id ?? "");
  const [vehicle, setVehicle] = useState(pass?.vehicle ?? "");
  const [siteId, setSiteId] = useState<number | "">(pass?.site_id ?? "");
  const [number, setNumber] = useState(pass?.number ?? "");
  const [validFrom, setValidFrom] = useState(pass?.valid_from ?? procurement.date_start ?? "");
  const [validTo, setValidTo] = useState(pass?.valid_to ?? procurement.deadline ?? "");
  const [status, setStatus] = useState<string>(pass?.status ?? "needed");
  const [note, setNote] = useState(pass?.note ?? "");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchEmployees().then(setEmployees).catch(() => undefined);
  }, []);

  const save = async () => {
    if (forWhat === "person" && !employeeId) return setError("Выберите сотрудника");
    if (forWhat === "car" && !vehicle.trim()) return setError("Укажите автомобиль");
    setBusy(true);
    setError("");
    const payload = {
      employee_id: forWhat === "person" ? Number(employeeId) : null,
      vehicle: forWhat === "car" ? vehicle.trim() : "",
      site_id: siteId ? Number(siteId) : null,
      number: number.trim(),
      valid_from: validFrom || null,
      valid_to: validTo || null,
      status,
      note: note.trim(),
    };
    try {
      if (pass) await updatePass(pass.id, payload);
      else await createPass(procurement.id, payload);
      onSaved();
    } catch (exc) {
      setError(errorText(exc, "Не удалось сохранить пропуск"));
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!pass || !(await confirmDialog({ title: "Удалить пропуск?", text: pass.holder, ok: "Удалить", danger: true }))) return;
    try {
      await deletePass(pass.id);
      onSaved();
    } catch (exc) {
      setError(errorText(exc, "Не удалось удалить"));
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={pass ? "Пропуск" : "Новый пропуск"}
      subtitle={procurement.customer}
      footer={
        <>
          {pass && <button type="button" className="ghost danger" onClick={() => void remove()}><Icon name="trash" size={15} /> Удалить</button>}
          <span className="spacer" />
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        </>
      }
    >
      <div className="form-stack">
        <div className="field-row">
          <div className="field" style={{ flex: "0 0 150px" }}>
            <label htmlFor="pass-for">На кого</label>
            <select id="pass-for" value={forWhat} onChange={(event) => setForWhat(event.target.value as "person" | "car")}>
              <option value="person">Сотрудник</option>
              <option value="car">Автомобиль</option>
            </select>
          </div>
          {forWhat === "person" ? (
            <div className="field">
              <label htmlFor="pass-employee">Сотрудник</label>
              <select id="pass-employee" value={employeeId} onChange={(event) => setEmployeeId(event.target.value ? Number(event.target.value) : "")}>
                <option value="">— выберите —</option>
                {employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name}</option>)}
              </select>
            </div>
          ) : (
            <div className="field">
              <label htmlFor="pass-vehicle">Марка и госномер</label>
              <input id="pass-vehicle" value={vehicle} onChange={(event) => setVehicle(event.target.value)} placeholder="Лада Ларгус, А123ВС 43" />
            </div>
          )}
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="pass-status">Состояние</label>
            <select id="pass-status" value={status} onChange={(event) => setStatus(event.target.value)}>
              {PASS_STATUSES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="pass-number">Номер <span className="opt">— если есть</span></label>
            <input id="pass-number" value={number} onChange={(event) => setNumber(event.target.value)} />
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="pass-from">Действует с</label>
            <input id="pass-from" type="date" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="pass-to">по</label>
            <input id="pass-to" type="date" value={validTo} min={validFrom} onChange={(event) => setValidTo(event.target.value)} />
          </div>
        </div>
        {procurement.sites.length > 0 && (
          <div className="field">
            <label htmlFor="pass-site">Объект</label>
            <select id="pass-site" value={siteId} onChange={(event) => setSiteId(event.target.value ? Number(event.target.value) : "")}>
              <option value="">Все объекты заказчика</option>
              {procurement.sites.map((site) => <option key={site.id} value={site.id}>{site.address}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="pass-note">Примечание</label>
          <input id="pass-note" value={note} maxLength={250} onChange={(event) => setNote(event.target.value)} placeholder="Получать на проходной № 2 по паспорту" />
        </div>
        {validTo && <div className="hint">В командировке будет видно, действует ли пропуск на её даты (сейчас — по {ruDate(validTo, true)}).</div>}
        {error && <Alert>{error}</Alert>}
      </div>
    </Modal>
  );
}
