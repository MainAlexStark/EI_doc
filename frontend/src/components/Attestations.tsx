import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, call, deleteAttestation, json, type Me } from "../api";
import Icon from "../ui/Icon";
import { confirmDialog, Modal } from "../ui/Modal";
import { Alert, EmptyState, PageActions, SearchField, Spinner } from "../ui/controls";
import { toast } from "../ui/Toast";
import { programFromAttestation, type Plan, type PlanCheck } from "../modes/att-templates/api";
import PlanEditor, { PlanCheckReport } from "../modes/att-templates/PlanEditor";

/**
 * Аттестация испытательного оборудования (холодильники, термостаты, шкафы…).
 *
 * Карточку и план обычно собирает ассистент (кнопка «Подтвердить» в чате),
 * метролог проверяет план. Измерения и условия вносит человек здесь — ни
 * ассистент, ни сервер их не придумывают. Расчёт и заключение — сервер.
 */

// Типы плана и редактор плана — общие с режимом «Шаблоны аттестации» (modes/att-templates).
type ModeMeasurements = { display: string; readings: Record<string, string[]> };
type Result = {
  modes: {
    key: string; label: string; passed: boolean;
    points: { key: string; mean: string }[];
    characteristics: {
      key: string; label: string; formula: string; shown: string; tolerance: string; passed: boolean | null;
      point: string; point_label: string;
    }[];
  }[];
  suitable: boolean;
};
type Equipment = Record<"name" | "serial_number" | "inventory_number" | "manufacturer" | "year" | "owner" | "location", string>;
type Member = { role: "chair" | "member"; position: string; name: string };
type Instrument = {
  name: string; serial_number: string; registry_number: string; characteristics: string; verification: string; valid_until: string;
};
type Summary = {
  id: number; number: string; status: "draft" | "signed"; kind: string; name: string; serial_number: string;
  owner: string; performed_on: string | null; protocol_date: string | null; suitable: boolean | null;
  plan_approved: boolean; created_via_assistant: boolean;
};
type Detail = Summary & {
  equipment: Equipment; commission: Member[]; instruments: Instrument[]; plan: Plan;
  texts: Record<string, string>; sources: string[]; conditions: Record<string, string>;
  measurements: Record<string, ModeMeasurements>; plan_approved_by: string; measurements_by: string;
  signed_by: string; sha256: string; program: string;
  calc: { complete: boolean; missing: string[]; results: Result | null };
  sign_problems: string[]; can_edit: boolean; can_review: boolean;
  plan_check: PlanCheck; plan_tolerances: Record<string, string>;
};
type Program = { id: number; title: string; equipment_kind: string; needs_review: boolean; note: string };

const KIND = { primary: "первичная", periodic: "периодическая", repeat: "повторная" } as const;
const EQUIPMENT_FIELDS: [keyof Equipment, string][] = [
  ["name", "Наименование"], ["serial_number", "Заводской номер"], ["inventory_number", "Инвентарный номер"],
  ["manufacturer", "Изготовитель"], ["year", "Год выпуска"], ["owner", "Владелец"], ["location", "Место установки"],
];
const TEXTS: [string, string][] = [
  ["documentation", "1. Экспертиза эксплуатационной документации"], ["inspection", "2. Внешний осмотр"],
  ["testing", "3. Опробование"], ["notes", "Дополнительно к методике"], ["recommendations", "Рекомендации комиссии"],
];

const errText = (e: unknown) => (e instanceof Error ? e.message : "Ошибка");
const ru = (d: string | null) => (d ? d.split("-").reverse().join(".") : "");
const comma = (v: string) => v.replace(".", ",");

async function download(id: number, fmt: "docx" | "pdf") {
  const response = await call(`/api/attestations/${id}/protocol.${fmt}`);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try { detail = (await response.json()).detail ?? detail; } catch { /* не JSON */ }
    throw new ApiError(detail, response.status);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? decodeURIComponent(match[1]) : `protocol.${fmt}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// ===========================================================================
export default function Attestations({
  me, initialOpenId = null, initialCreate = false, onProgramCreated,
}: {
  me: Me | null;
  /** Сразу открыть аттестацию (из режима «Шаблоны аттестации» — «Применить к новому ИО»). */
  initialOpenId?: number | null;
  /** Сразу открыть окно «Новая аттестация». */
  initialCreate?: boolean;
  /** Из плана аттестации сделан шаблон — режим откроет его. */
  onProgramCreated?: (programId: number) => void;
}) {
  const [openId, setOpenId] = useState<number | null>(initialOpenId);
  if (openId) return <AttestationView id={openId} onBack={() => setOpenId(null)} onProgramCreated={onProgramCreated} />;
  return <AttestationList me={me} onOpen={setOpenId} initialCreate={initialCreate} />;
}

function AttestationList({ me, onOpen, initialCreate }: { me: Me | null; onOpen: (id: number) => void; initialCreate?: boolean }) {
  const [items, setItems] = useState<Summary[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(!!initialCreate);
  const canCreate = me?.role !== "observer";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await json<Summary[]>(`/api/attestations/?q=${encodeURIComponent(query.trim())}`));
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 250);
    return () => clearTimeout(timer);
  }, [load]);

  return (
    <div className="narrow attestation">
      {canCreate && (
        <PageActions>
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            <Icon name="plus" size={16} strokeWidth={2.2} /> Новая аттестация
          </button>
        </PageActions>
      )}
      <div className="toolbar">
        <SearchField className="grow" value={query} onChange={setQuery} placeholder="Наименование, заводской номер, владелец" />
      </div>
      <Alert tone="info">
        Быстрее всего — попросить ассистента: «заведи аттестацию холодильника ХФ-140 зав. № …» с фото шильдика
        или прошлым протоколом. Он соберёт карточку и план, измерения вносятся здесь.
      </Alert>
      <Modal open={creating} onClose={() => setCreating(false)} size="lg" title="Новая аттестация" subtitle="Карточку оборудования можно дополнить позже">
        <CreateForm onCreated={(id) => { setCreating(false); onOpen(id); }} onCancel={() => setCreating(false)} />
      </Modal>
      {error && <Alert>{error}</Alert>}
      {loading && !items.length ? <Spinner /> : !items.length ? (
        <EmptyState icon="shield" title={query ? "Ничего не нашлось" : "Аттестаций пока нет"}>
          {!query && "Испытательное оборудование (холодильники, термостаты, шкафы) аттестуется здесь — по программе и с расчётом."}
        </EmptyState>
      ) : (
        <div className="card-list">
          {items.map((a) => (
            <button key={a.id} type="button" className="card tap" onClick={() => onOpen(a.id)}>
              <div className="card-head">
                <div className="grow">
                  <b>{a.name}</b> <span className="sub">зав. № {a.serial_number}</span>
                  <div className="sub">{a.owner}</div>
                </div>
                <StatusPills a={a} />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusPills({ a }: { a: Summary }) {
  return (
    <div className="pills">
      {a.status === "signed"
        ? <span className="pill ok">№ {a.number} от {ru(a.protocol_date)}</span>
        : <span className="pill muted">черновик</span>}
      {a.status === "signed" && (a.suitable
        ? <span className="pill ok">соответствует</span>
        : <span className="pill bad">не соответствует</span>)}
      {a.status === "draft" && !a.plan_approved && <span className="pill warn">план ждёт метролога</span>}
      {a.created_via_assistant && <span className="pill muted">от ассистента</span>}
    </div>
  );
}

function CreateForm({ onCreated, onCancel }: { onCreated: (id: number) => void; onCancel: () => void }) {
  const [programs, setPrograms] = useState<Program[]>([]);
  const [program, setProgram] = useState<number | "">("");
  const [equipment, setEquipment] = useState<Equipment>({
    name: "", serial_number: "", inventory_number: "", manufacturer: "", year: "", owner: "", location: "",
  });
  const [kind, setKind] = useState("periodic");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void json<Program[]>("/api/attestations/programs/").then((list) => {
      setPrograms(list);
      if (list.length === 1) setProgram(list[0].id);
    }).catch((e) => setError(errText(e)));
  }, []);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await json<Detail>("/api/attestations/", {
        method: "POST", body: JSON.stringify({ program, equipment, kind }),
      });
      onCreated(created.id);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="create-attestation">
      <div className="form-grid">
        <div className="field wide">
          <label>Программа (заготовка плана)</label>
          <select value={program} onChange={(e) => setProgram(e.target.value ? Number(e.target.value) : "")}>
            <option value="">— выберите —</option>
            {programs.map((p) => <option key={p.id} value={p.id}>{p.title}{p.needs_review ? " (не сверена)" : ""}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Вид аттестации</label>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {EQUIPMENT_FIELDS.map(([key, label]) => (
          <div key={key} className="field">
            <label>{label}</label>
            <input value={equipment[key]} onChange={(e) => setEquipment({ ...equipment, [key]: e.target.value })} />
          </div>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="actions end">
        <button type="button" onClick={onCancel}>Отмена</button>
        <button type="button" className="primary" disabled={busy || !program} onClick={() => void submit()}>
          {busy ? "Создаю…" : "Создать"}
        </button>
      </div>
    </div>
  );
}

// ===========================================================================
function AttestationView({ id, onBack, onProgramCreated }: { id: number; onBack: () => void; onProgramCreated?: (programId: number) => void }) {
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [signDate, setSignDate] = useState(() => new Date().toLocaleDateString("sv-SE"));

  const load = useCallback(async () => {
    try {
      setData(await json<Detail>(`/api/attestations/${id}/`));
    } catch (e) {
      setError(errText(e));
    }
  }, [id]);
  useEffect(() => void load(), [load]);

  const run = async (fn: () => Promise<Detail | void>) => {
    setBusy(true);
    setError("");
    try {
      const result = await fn();
      if (result) setData(result);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <div className="narrow">{error ? <Alert>{error}</Alert> : <Spinner />}</div>;

  const post = (path: string, body: unknown = {}) =>
    json<Detail>(`/api/attestations/${id}/${path}`, { method: "POST", body: JSON.stringify(body) });

  return (
    <div className="narrow attestation wide-narrow">
      <PageActions>
        <button type="button" disabled={busy} onClick={() => void run(() => download(id, "docx"))}><Icon name="download" size={16} /> Word</button>
        <button type="button" disabled={busy} onClick={() => void run(() => download(id, "pdf"))}><Icon name="download" size={16} /> PDF</button>
        {onProgramCreated && (
          <button type="button" disabled={busy} title="План этой аттестации станет шаблоном для других ИО того же вида"
            onClick={() => void run(async () => {
              const program = await programFromAttestation(id);
              toast.success("Шаблон сделан по аттестации", { description: program.title, action: { label: "Открыть", onClick: () => onProgramCreated(program.id) } });
            })}>
            <Icon name="layers" size={16} /> Сделать шаблоном
          </button>
        )}
        {data.status === "draft" && data.can_edit && (
          <button type="button" className="ghost danger" disabled={busy}
            onClick={() => void (async () => {
              const ok = await confirmDialog({
                title: "Удалить черновик аттестации?",
                text: `${data.equipment.name} № ${data.equipment.serial_number} — с планом и измерениями. Номер ещё не выдан, дыры в нумерации не будет.`,
                danger: true,
              });
              if (!ok) return;
              try {
                await deleteAttestation(id);
                toast.success("Черновик аттестации удалён");
                onBack();
              } catch (e) {
                setError(errText(e));
              }
            })()}>
            <Icon name="trash" size={16} /> Удалить
          </button>
        )}
      </PageActions>
      <button type="button" className="back" onClick={onBack}>
        <Icon name="chevronLeft" size={20} /> Все аттестации
      </button>
      <div className="card">
        <div className="card-head">
          <div className="grow">
            <h3>{data.equipment.name}</h3>
            <div className="sub">
              зав. № {data.equipment.serial_number} · {KIND[data.kind as keyof typeof KIND]} аттестация
              {data.program && <> · заготовка: {data.program}</>}
            </div>
          </div>
          <StatusPills a={data} />
        </div>
        {data.status === "signed" && (
          <p className="sub">Подписал {data.signed_by}. SHA-256 содержимого: <code>{data.sha256.slice(0, 16)}…</code></p>
        )}
      </div>
      {error && <div className="error">{error}</div>}

      <CardEditor data={data} busy={busy} onSave={(patch) => run(() => json<Detail>(`/api/attestations/${id}/`, {
        method: "PATCH", body: JSON.stringify(patch),
      }))} />
      <PlanView data={data} busy={busy} onApprove={() => run(() => post("approve-plan/"))}
        onSave={(plan) => run(() => json<Detail>(`/api/attestations/${id}/`, { method: "PATCH", body: JSON.stringify({ plan }) }))} />
      <MeasurementsEditor data={data} busy={busy} onSave={(body) => run(() => json<Detail>(`/api/attestations/${id}/measurements/`, {
        method: "PUT", body: JSON.stringify(body),
      }))} />
      <ResultsView data={data} />

      {data.status === "draft" && (
        <div className="card">
          <h3>Подпись</h3>
          {data.sign_problems.length > 0 ? (
            <ul className="problems">{data.sign_problems.map((p) => <li key={p}>{p}</li>)}</ul>
          ) : <p className="sub">Всё готово. Номер выдаётся при подписи.</p>}
          {data.can_review && (
            <div className="actions">
              <input type="date" value={signDate} onChange={(e) => setSignDate(e.target.value)} />
              <button type="button" className="primary" disabled={busy || data.sign_problems.length > 0}
                onClick={() => void run(() => post("sign/", { protocol_date: signDate }))}>Подписать протокол</button>
            </div>
          )}
        </div>
      )}

      {data.sources.length > 0 && (
        <details className="card">
          <summary>Откуда ассистент взял данные ({data.sources.length})</summary>
          <ul className="small">{data.sources.map((s, i) => <li key={i}>{s}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function CardEditor({ data, busy, onSave }: {
  data: Detail; busy: boolean; onSave: (patch: Partial<Detail>) => void;
}) {
  const [equipment, setEquipment] = useState(data.equipment);
  const [commission, setCommission] = useState(data.commission);
  const [instruments, setInstruments] = useState(data.instruments);
  const [texts, setTexts] = useState(data.texts);
  useEffect(() => {
    setEquipment(data.equipment);
    setCommission(data.commission);
    setInstruments(data.instruments);
    setTexts(data.texts);
  }, [data]);
  const ro = !data.can_edit;

  return (
    <details className="card" open={data.status === "draft" && (!data.commission.length || !data.instruments.length)}>
      <summary><b>Карточка</b> <span className="sub">оборудование, комиссия, средства измерений, тексты</span></summary>
      <h4>Оборудование</h4>
      <div className="form-grid">
        {EQUIPMENT_FIELDS.map(([key, label]) => (
          <div key={key} className="field">
            <label>{label}</label>
            <input disabled={ro} value={equipment[key] ?? ""} onChange={(e) => setEquipment({ ...equipment, [key]: e.target.value })} />
          </div>
        ))}
      </div>

      <h4>Комиссия</h4>
      {commission.map((m, i) => (
        <div key={i} className="row-edit">
          <select disabled={ro} value={m.role} onChange={(e) => setCommission(commission.map((x, j) => j === i ? { ...x, role: e.target.value as Member["role"] } : x))}>
            <option value="chair">Председатель</option>
            <option value="member">Член</option>
          </select>
          <input disabled={ro} className="grow" placeholder="Должность, организация" value={m.position}
            onChange={(e) => setCommission(commission.map((x, j) => j === i ? { ...x, position: e.target.value } : x))} />
          <input disabled={ro} placeholder="Фамилия И. О." value={m.name}
            onChange={(e) => setCommission(commission.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
          {!ro && <button type="button" onClick={() => setCommission(commission.filter((_, j) => j !== i))}>×</button>}
        </div>
      ))}
      {!ro && <button type="button" onClick={() => setCommission([...commission, { role: commission.length ? "member" : "chair", position: "", name: "" }])}>+ Член комиссии</button>}

      <h4>Средства измерений</h4>
      {instruments.map((r, i) => (
        <div key={i} className="instrument">
          {([["name", "Наименование, тип"], ["serial_number", "Зав. №"], ["characteristics", "Диапазон, погрешность"],
            ["verification", "Свидетельство о поверке"]] as [keyof Instrument, string][]).map(([key, label]) => (
            <input key={key} disabled={ro} placeholder={label} value={r[key]}
              onChange={(e) => setInstruments(instruments.map((x, j) => j === i ? { ...x, [key]: e.target.value } : x))} />
          ))}
          <label className="sub">годен до <input type="date" disabled={ro} value={r.valid_until}
            onChange={(e) => setInstruments(instruments.map((x, j) => j === i ? { ...x, valid_until: e.target.value } : x))} /></label>
          {!ro && <button type="button" onClick={() => setInstruments(instruments.filter((_, j) => j !== i))}>Убрать</button>}
        </div>
      ))}
      {!ro && <button type="button" onClick={() => setInstruments([...instruments, {
        name: "", serial_number: "", registry_number: "", characteristics: "", verification: "", valid_until: "",
      }])}>+ Средство измерений</button>}

      <h4>Текстовые пункты</h4>
      {TEXTS.map(([key, label]) => (
        <div key={key} className="field">
          <label>{label}</label>
          <textarea disabled={ro} rows={2} value={texts[key] ?? ""} placeholder={key === "notes" || key === "recommendations" ? "" : "по умолчанию — типовая формулировка"}
            onChange={(e) => setTexts({ ...texts, [key]: e.target.value })} />
        </div>
      ))}
      {!ro && (
        <div className="actions">
          <button type="button" className="primary" disabled={busy}
            onClick={() => onSave({ equipment, commission, instruments, texts })}>Сохранить карточку</button>
        </div>
      )}
    </details>
  );
}

// ---------------------------------------------------------------------------
function PlanView({ data, busy, onApprove, onSave }: {
  data: Detail; busy: boolean; onApprove: () => void; onSave: (plan: unknown) => void;
}) {
  const [plan, setPlan] = useState<Plan>(data.plan);
  useEffect(() => setPlan(data.plan), [data.plan]);
  const dirty = JSON.stringify(plan) !== JSON.stringify(data.plan);

  return (
    <details className="card" open={!data.plan_approved}>
      <summary>
        <b>План аттестации</b>{" "}
        {data.plan_approved
          ? <span className="pill ok">проверил {data.plan_approved_by}</span>
          : <span className="pill warn">ждёт проверки метролога</span>}
        {data.plan_check?.rows && (data.plan_check.stale
          ? <span className="pill warn">сверка устарела</span>
          : data.plan_check.ok ? <span className="pill ok">формулы сходятся</span> : <span className="pill bad">формулы не сходятся</span>)}
      </summary>
      <p className="sub">
        {data.plan.program} · {data.plan.quantity}, {data.plan.unit} · режимов {data.plan.modes.length} · точек {data.plan.points.length} ·
        измерений в точке {data.plan.repeats}{data.plan.modes.length ? ` · ${data.plan.modes.map((m) => `${m.label} (задано ${comma(m.setpoint)})`).join(", ")}` : ""}
      </p>
      <PlanEditor plan={plan} onChange={setPlan} readOnly={!data.can_edit} check={data.plan_check} tolerances={data.plan_tolerances} />
      <div className="actions">
        {data.can_edit && (
          <button type="button" disabled={busy || !dirty} onClick={() => onSave(plan)} title="После правки план снова проверяет метролог">
            Сохранить план
          </button>
        )}
        {dirty && <span className="sub">есть несохранённые правки — после сохранения план снова проверяет метролог</span>}
        {data.can_review && !data.plan_approved && (
          <button type="button" className="primary" disabled={busy || dirty} onClick={onApprove}>План проверен</button>
        )}
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
function emptyGrid(plan: Plan, current: Record<string, ModeMeasurements>) {
  const grid: Record<string, ModeMeasurements> = {};
  for (const mode of plan.modes) {
    const m = current[mode.key];
    grid[mode.key] = { display: m?.display ?? "", readings: {} };
    for (const p of plan.points) {
      const values = m?.readings?.[p.key] ?? [];
      grid[mode.key].readings[p.key] = Array.from({ length: plan.repeats }, (_, i) => values[i] ?? "");
    }
  }
  return grid;
}

function MeasurementsEditor({ data, busy, onSave }: {
  data: Detail; busy: boolean; onSave: (body: unknown) => void;
}) {
  const plan = data.plan;
  const [grid, setGrid] = useState(() => emptyGrid(plan, data.measurements));
  const [conditions, setConditions] = useState(data.conditions);
  const [performedOn, setPerformedOn] = useState(data.performed_on ?? "");
  useEffect(() => {
    setGrid(emptyGrid(data.plan, data.measurements));
    setConditions(data.conditions);
    setPerformedOn(data.performed_on ?? "");
  }, [data]);
  const ro = !data.can_edit;
  const filled = useMemo(() => Object.values(grid).reduce(
    (n, m) => n + Object.values(m.readings).flat().filter((v) => v.trim()).length, 0), [grid]);
  const total = plan.modes.length * plan.points.length * plan.repeats;

  const setCell = (mode: string, point: string, i: number, value: string) => setGrid((g) => ({
    ...g, [mode]: { ...g[mode], readings: { ...g[mode].readings, [point]: g[mode].readings[point].map((v, j) => j === i ? value : v) } },
  }));

  /** Вставка столбца/строки из Excel: значения через табуляцию или перевод строки — подряд по точке. */
  const paste = (mode: string, point: string, start: number, text: string) => {
    const values = text.trim().split(/[\t\n\r;]+/).map((v) => v.trim()).filter(Boolean);
    if (values.length < 2) return false;
    setGrid((g) => ({
      ...g, [mode]: { ...g[mode], readings: { ...g[mode].readings, [point]: g[mode].readings[point].map((v, j) =>
        j >= start && j - start < values.length ? values[j - start] : v) } },
    }));
    return true;
  };

  return (
    <div className="card">
      <h3>Измерения <span className="sub">внесено {filled} из {total}</span></h3>
      <p className="sub">Только фактически снятые показания. Из Excel можно вставить ряд значений точки целиком.</p>
      <div className="form-grid">
        <div className="field">
          <label>Дата измерений</label>
          <input type="date" disabled={ro} value={performedOn} onChange={(e) => setPerformedOn(e.target.value)} />
        </div>
        {(plan.conditions ?? []).map(({ key: k, label, unit }) => (
          <div key={k} className="field">
            <label>{label}{unit ? `, ${unit}` : ""}</label>
            <input inputMode="decimal" disabled={ro} value={conditions[k] ?? ""}
              onChange={(e) => setConditions({ ...conditions, [k]: e.target.value })} />
          </div>
        ))}
      </div>
      {plan.modes.map((mode) => (
        <div key={mode.key} className="mode">
          <h4>
            {plan.quantity}: задано {comma(mode.setpoint)} {plan.unit}{" "}
            <label className="sub">показание индикатора ИО{" "}
              <input className="cell" inputMode="decimal" disabled={ro} value={grid[mode.key].display}
                onChange={(e) => setGrid({ ...grid, [mode.key]: { ...grid[mode.key], display: e.target.value } })} />
            </label>
          </h4>
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr><th>Точка</th>{Array.from({ length: plan.repeats }, (_, i) => <th key={i}>{i + 1}</th>)}</tr>
              </thead>
              <tbody>
                {plan.points.map((p) => (
                  <tr key={p.key} className={p.role === "control" ? "control" : ""}>
                    <th>{p.label}</th>
                    {grid[mode.key].readings[p.key].map((v, i) => (
                      <td key={i}>
                        <input className="cell" inputMode="decimal" disabled={ro} value={v}
                          onChange={(e) => setCell(mode.key, p.key, i, e.target.value)}
                          onPaste={(e) => { if (paste(mode.key, p.key, i, e.clipboardData.getData("text"))) e.preventDefault(); }} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      {!ro && (
        <div className="actions">
          <button type="button" className="primary" disabled={busy}
            onClick={() => onSave({ measurements: grid, conditions, performed_on: performedOn || null })}>
            Сохранить и рассчитать
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function ResultsView({ data }: { data: Detail }) {
  const { calc } = data;
  if (!calc.results) {
    return (
      <div className="card">
        <h3>Расчёт</h3>
        <p className="sub">Расчёт появится, когда сетка заполнена целиком.</p>
        {calc.missing.length > 0 && <ul className="problems">{calc.missing.slice(0, 8).map((m) => <li key={m}>{m}</li>)}</ul>}
      </div>
    );
  }
  return (
    <div className="card">
      <h3>
        Расчёт{" "}
        {calc.results.suitable ? <span className="pill ok">соответствует</span> : <span className="pill bad">не соответствует</span>}
      </h3>
      {calc.results.modes.map((m) => (
        <div key={m.key} className="table-wrap">
          <table className="compact">
            <thead><tr><th>{m.label}</th><th>Формула</th><th>Значение</th><th>Допуск</th><th /></tr></thead>
            <tbody>
              {m.characteristics.map((c) => (
                <tr key={c.key + c.point}>
                  <td>{c.label}{c.point && <span className="sub">, точка {c.point_label}</span>}</td>
                  <td className="sub">{c.formula}</td>
                  <td className="num">{c.shown}</td>
                  <td>{c.tolerance}</td>
                  <td>{c.passed === null ? "" : c.passed ? <span className="pill ok">да</span> : <span className="pill bad">нет</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

// Отчёт сверки — PlanCheckReport (modes/att-templates/PlanEditor).
export { PlanCheckReport };
