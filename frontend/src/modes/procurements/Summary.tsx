/** Вкладка «Выжимка»: что требует контракт — понятными блоками, правится руками или ассистентом. */

import { useCallback, useEffect, useState } from "react";
import { errorText, ruDate, ruShort } from "../../components/tripUtil";
import { EmptyState } from "../../ui/EmptyState";
import { useHotkey } from "../../ui/Hotkeys";
import Icon, { type IconName } from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { SkeletonText } from "../../ui/Skeleton";
import toast from "../../ui/Toast";
import {
  fetchSummary,
  normalizeSummary,
  saveSummary,
  type Summary,
  type SummaryContact,
  type SummaryInstrument,
  type SummaryResponse,
  type SummaryStage,
  type TextPoint,
} from "./api";

type TextKey = "requirements" | "access" | "penalties";

const TEXT_BLOCKS: { key: TextKey; title: string; icon: IconName; empty: string; placeholder: string }[] = [
  { key: "requirements", title: "Требования к исполнителю", icon: "checkCircle", empty: "Требования не выписаны", placeholder: "Аккредитация в области поверки счётчиков воды" },
  { key: "access", title: "Пропуска и допуск", icon: "lock", empty: "Про пропуска ничего не выписано", placeholder: "Список сотрудников и машин — за 5 рабочих дней до выезда" },
  { key: "penalties", title: "Штрафы и неустойки", icon: "alert", empty: "Штрафы не выписаны", placeholder: "Пеня 1/300 ключевой ставки за каждый день просрочки" },
];

const isEmpty = (s: Summary) =>
  !s.requirements.length && !s.instruments.length && !s.stages.length && !s.access.length &&
  !s.contacts.length && !s.penalties.length && !s.deadline && !s.notes;

export default function SummaryTab({
  procurementId, canEdit, onAskAssistant, version,
}: {
  procurementId: number;
  canEdit: boolean;
  onAskAssistant: () => void;
  /** Растёт, когда ассистент мог записать выжимку, — перечитать. */
  version: number;
}) {
  const [data, setData] = useState<SummaryResponse | null>(null);
  const [draft, setDraft] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError("");
    fetchSummary(procurementId)
      .then(setData)
      .catch((exc) => setError(errorText(exc, "Не удалось загрузить выжимку")));
  }, [procurementId]);

  useEffect(() => {
    load();
  }, [load, version]);

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const previous = data ? normalizeSummary(data.summary) : null;
      const clean: Summary = {
        ...draft,
        requirements: draft.requirements.filter((p) => p.text.trim()),
        access: draft.access.filter((p) => p.text.trim()),
        penalties: draft.penalties.filter((p) => p.text.trim()),
        instruments: draft.instruments.filter((r) => r.name.trim()).map((r) => ({ ...r, quantity: Math.max(1, Number(r.quantity) || 1) })),
        stages: draft.stages.filter((r) => r.title.trim()),
        contacts: draft.contacts.filter((r) => (r.name || r.phone || r.email || "").trim()),
      };
      const fresh = await saveSummary(procurementId, clean);
      setData(fresh);
      setDraft(null);
      toast.success("Выжимка сохранена", previous ? {
        undo: async () => {
          setData(await saveSummary(procurementId, previous));
          toast.info("Вернул прежнюю выжимку");
        },
      } : undefined);
    } catch (exc) {
      toast.error("Не сохранилось", { description: errorText(exc, "Проверьте даты (ГГГГ-ММ-ДД) и количества") });
    } finally {
      setBusy(false);
    }
  };

  useHotkey("mod+enter", () => void save(), { enabled: !!draft, allowInInputs: true, description: "Сохранить выжимку", group: "Закупки" });
  useHotkey("e", () => data && setDraft(normalizeSummary(data.summary)), { enabled: canEdit && !draft && !!data, description: "Править выжимку", group: "Закупки" });

  if (error) return <EmptyState tone="error" title="Выжимка не загрузилась" action={{ label: "Повторить", onClick: load }}>{error}</EmptyState>;
  if (!data) return <div className="pr-pad"><SkeletonText lines={6} /></div>;

  const summary = draft ?? normalizeSummary(data.summary);
  const editing = draft != null;
  const set = (patch: Partial<Summary>) => setDraft((current) => ({ ...(current ?? summary), ...patch }));

  if (!editing && isEmpty(summary)) {
    return (
      <EmptyState
        icon="clipboard"
        title="Выжимки пока нет"
        action={canEdit ? { label: "Собрать ассистентом", icon: "sparkle", onClick: onAskAssistant } : undefined}
        secondary={canEdit ? { label: "Заполнить вручную", icon: "pen", onClick: () => setDraft(summary) } : undefined}
      >
        {canEdit
          ? "Ассистент прочитает ТЗ и контракт и выпишет требования, перечень СИ с адресами, сроки, пропуска, контакты и штрафы — со ссылками на пункты. Перед записью покажет, что сохранит."
          : "Руководитель или менеджер ещё не собрал выжимку по документам. Сами документы — на вкладке «Документы»."}
      </EmptyState>
    );
  }

  return (
    <div className="pr-summary">
      <div className="pr-summary-bar">
        <div className="sub grow">
          {data.saved_at
            ? <>Сохранил {data.saved_by || "—"} · {new Date(data.saved_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}{data.via_assistant && <span className="pill info plain">ассистент</span>}</>
            : "Ещё не сохранена"}
        </div>
        {canEdit && !editing && (
          <>
            <button type="button" onClick={onAskAssistant}><Icon name="sparkle" size={15} /> Собрать ассистентом</button>
            <button type="button" onClick={() => setDraft(summary)}><Icon name="pen" size={15} /> Править <Kbd className="kbd-hint">E</Kbd></button>
          </>
        )}
        {editing && (
          <>
            <button type="button" className="ghost" onClick={() => setDraft(null)}>Отмена</button>
            <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
              {busy ? "Сохраняю…" : "Сохранить"} <Kbd className="kbd-hint" keys="mod+enter" />
            </button>
          </>
        )}
      </div>

      <section className="pr-block">
        <h3><Icon name="clock" size={16} /> Срок исполнения</h3>
        {editing ? (
          <div className="field-row">
            <input type="date" value={summary.deadline} onChange={(e) => set({ deadline: e.target.value })} aria-label="Срок исполнения" style={{ maxWidth: 200 }} />
          </div>
        ) : (
          <p>{summary.deadline ? <b className="data">{ruDate(summary.deadline, true)}</b> : <span className="sub">не выписан</span>}</p>
        )}
      </section>

      <InstrumentsBlock rows={summary.instruments} editing={editing} onChange={(instruments) => set({ instruments })} />
      <StagesBlock rows={summary.stages} editing={editing} onChange={(stages) => set({ stages })} />
      {TEXT_BLOCKS.map((block) => (
        <PointsBlock
          key={block.key}
          title={block.title}
          icon={block.icon}
          empty={block.empty}
          placeholder={block.placeholder}
          rows={summary[block.key]}
          editing={editing}
          onChange={(rows) => set({ [block.key]: rows } as Partial<Summary>)}
        />
      ))}
      <ContactsBlock rows={summary.contacts} editing={editing} onChange={(contacts) => set({ contacts })} />

      {(editing || summary.notes) && (
        <section className="pr-block">
          <h3><Icon name="doc" size={16} /> Заметки</h3>
          {editing
            ? <textarea rows={3} value={summary.notes} onChange={(e) => set({ notes: e.target.value })} />
            : <p className="pr-notes">{summary.notes}</p>}
        </section>
      )}
      {data.sources?.length > 0 && !editing && (
        <p className="sub pr-sources">Источники: {data.sources.join(" · ")}</p>
      )}
    </div>
  );
}

function Source({ text }: { text?: string }) {
  return text ? <span className="pr-source" title="Откуда взято">{text}</span> : null;
}

function PointsBlock({
  title, icon, empty, placeholder, rows, editing, onChange,
}: {
  title: string;
  icon: IconName;
  empty: string;
  placeholder: string;
  rows: TextPoint[];
  editing: boolean;
  onChange: (rows: TextPoint[]) => void;
}) {
  if (!editing && !rows.length) {
    return (
      <section className="pr-block is-empty">
        <h3><Icon name={icon} size={16} /> {title}</h3>
        <p className="sub">{empty}</p>
      </section>
    );
  }
  return (
    <section className="pr-block">
      <h3><Icon name={icon} size={16} /> {title} {rows.length > 0 && <span className="count">{rows.length}</span>}</h3>
      {editing ? (
        <div className="pr-edit-rows">
          {rows.map((row, index) => (
            <div className="pr-edit-row" key={index}>
              <textarea rows={2} value={row.text} placeholder={placeholder}
                        onChange={(e) => onChange(rows.map((r, i) => (i === index ? { ...r, text: e.target.value } : r)))} />
              <input className="pr-src-input" value={row.source ?? ""} placeholder="Источник: ТЗ п. 4.2"
                     onChange={(e) => onChange(rows.map((r, i) => (i === index ? { ...r, source: e.target.value } : r)))} />
              <button type="button" className="icon-btn sm" aria-label="Убрать пункт" onClick={() => onChange(rows.filter((_, i) => i !== index))}>
                <Icon name="x" size={14} />
              </button>
            </div>
          ))}
          <button type="button" className="sm" onClick={() => onChange([...rows, { text: "", source: "" }])}><Icon name="plus" size={14} /> Пункт</button>
        </div>
      ) : (
        <ul className="pr-points">
          {rows.map((row, index) => <li key={index}>{row.text} <Source text={row.source} /></li>)}
        </ul>
      )}
    </section>
  );
}

function InstrumentsBlock({ rows, editing, onChange }: { rows: SummaryInstrument[]; editing: boolean; onChange: (rows: SummaryInstrument[]) => void }) {
  const total = rows.reduce((sum, row) => sum + (Number(row.quantity) || 0), 0);
  const patch = (index: number, value: Partial<SummaryInstrument>) => onChange(rows.map((r, i) => (i === index ? { ...r, ...value } : r)));
  if (!editing && !rows.length) {
    return (
      <section className="pr-block is-empty">
        <h3><Icon name="gauge" size={16} /> Перечень СИ</h3>
        <p className="sub">Перечня нет — без него план заходов возьмёт объём по контракту.</p>
      </section>
    );
  }
  return (
    <section className="pr-block">
      <h3><Icon name="gauge" size={16} /> Перечень СИ <span className="count">{total} шт.</span></h3>
      <div className="table-wrap">
        <table className="data-table pr-table">
          <thead><tr><th>Наименование</th><th>Тип / № ГРСИ</th><th className="num">Кол-во</th><th>Адрес объекта</th>{editing && <th />}</tr></thead>
          <tbody>
            {rows.map((row, index) => editing ? (
              <tr key={index}>
                <td><input value={row.name} onChange={(e) => patch(index, { name: e.target.value })} placeholder="Счётчик воды Ду15" /></td>
                <td><input value={row.type ?? ""} onChange={(e) => patch(index, { type: e.target.value })} placeholder="СГВ-15" /></td>
                <td className="num"><input className="data" inputMode="numeric" value={row.quantity} onChange={(e) => patch(index, { quantity: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></td>
                <td><input value={row.address ?? ""} onChange={(e) => patch(index, { address: e.target.value })} placeholder="г. Киров, ул. …" /></td>
                <td><button type="button" className="icon-btn sm" aria-label="Убрать строку" onClick={() => onChange(rows.filter((_, i) => i !== index))}><Icon name="x" size={14} /></button></td>
              </tr>
            ) : (
              <tr key={index}>
                <td>{row.name}{row.note && <div className="sub">{row.note}</div>}</td>
                <td>{[row.type, row.registry_number].filter(Boolean).join(" · ") || "—"}</td>
                <td className="num data">{row.quantity}</td>
                <td>{row.address || <span className="signal-text">адрес не указан</span>} <Source text={row.source} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && <button type="button" className="sm" onClick={() => onChange([...rows, { name: "", quantity: 1, address: "" }])}><Icon name="plus" size={14} /> Строка</button>}
    </section>
  );
}

function StagesBlock({ rows, editing, onChange }: { rows: SummaryStage[]; editing: boolean; onChange: (rows: SummaryStage[]) => void }) {
  const patch = (index: number, value: Partial<SummaryStage>) => onChange(rows.map((r, i) => (i === index ? { ...r, ...value } : r)));
  if (!editing && !rows.length) {
    return (
      <section className="pr-block is-empty">
        <h3><Icon name="calendar" size={16} /> Сроки и этапы</h3>
        <p className="sub">Этапы не выписаны</p>
      </section>
    );
  }
  return (
    <section className="pr-block">
      <h3><Icon name="calendar" size={16} /> Сроки и этапы</h3>
      {editing ? (
        <div className="pr-edit-rows">
          {rows.map((row, index) => (
            <div className="pr-edit-row stage" key={index}>
              <input value={row.title} onChange={(e) => patch(index, { title: e.target.value })} placeholder="Этап 1 — поверка в Кирове" />
              <input type="date" value={row.date_from ?? ""} onChange={(e) => patch(index, { date_from: e.target.value })} aria-label="С" />
              <input type="date" value={row.date_to ?? ""} onChange={(e) => patch(index, { date_to: e.target.value })} aria-label="По" />
              <button type="button" className="icon-btn sm" aria-label="Убрать этап" onClick={() => onChange(rows.filter((_, i) => i !== index))}><Icon name="x" size={14} /></button>
            </div>
          ))}
          <button type="button" className="sm" onClick={() => onChange([...rows, { title: "" }])}><Icon name="plus" size={14} /> Этап</button>
        </div>
      ) : (
        <ol className="pr-stages">
          {rows.map((row, index) => (
            <li key={index}>
              <span className="data pr-stage-dates">{[ruShort(row.date_from), ruShort(row.date_to)].filter(Boolean).join(" — ") || "—"}</span>
              <span>{row.title}{row.note && <div className="sub">{row.note}</div>} <Source text={row.source} /></span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function ContactsBlock({ rows, editing, onChange }: { rows: SummaryContact[]; editing: boolean; onChange: (rows: SummaryContact[]) => void }) {
  const patch = (index: number, value: Partial<SummaryContact>) => onChange(rows.map((r, i) => (i === index ? { ...r, ...value } : r)));
  if (!editing && !rows.length) {
    return (
      <section className="pr-block is-empty">
        <h3><Icon name="users" size={16} /> Контакты заказчика</h3>
        <p className="sub">Контактов нет</p>
      </section>
    );
  }
  return (
    <section className="pr-block">
      <h3><Icon name="users" size={16} /> Контакты заказчика</h3>
      {editing ? (
        <div className="pr-edit-rows">
          {rows.map((row, index) => (
            <div className="pr-edit-row contact" key={index}>
              <input value={row.name ?? ""} onChange={(e) => patch(index, { name: e.target.value })} placeholder="ФИО" />
              <input value={row.position ?? ""} onChange={(e) => patch(index, { position: e.target.value })} placeholder="Должность" />
              <input type="tel" value={row.phone ?? ""} onChange={(e) => patch(index, { phone: e.target.value })} placeholder="Телефон" />
              <input type="email" value={row.email ?? ""} onChange={(e) => patch(index, { email: e.target.value })} placeholder="Почта" />
              <button type="button" className="icon-btn sm" aria-label="Убрать контакт" onClick={() => onChange(rows.filter((_, i) => i !== index))}><Icon name="x" size={14} /></button>
            </div>
          ))}
          <button type="button" className="sm" onClick={() => onChange([...rows, { name: "" }])}><Icon name="plus" size={14} /> Контакт</button>
        </div>
      ) : (
        <div className="pr-contacts">
          {rows.map((row, index) => (
            <div className="pr-contact" key={index}>
              <b>{row.name || "—"}</b>
              {row.position && <div className="sub">{row.position}</div>}
              <div className="pr-contact-links">
                {row.phone && <a href={`tel:${row.phone.replace(/[^\d+]/g, "")}`}><Icon name="phone" size={13} /> <span className="data">{row.phone}</span></a>}
                {row.email && <a href={`mailto:${row.email}`}><Icon name="mail" size={13} /> {row.email}</a>}
              </div>
              {row.note && <div className="sub">{row.note}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
