/** Один шаблон плана аттестации: план таблицами, сверка формул с примером, JSON для специалиста;
 *  «Проверен» (метролог) и «Применить к новому ИО». ⌘S — сохранить правки. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { confirmDialog } from "../../ui/Modal";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { Sheet } from "../../ui/Sheet";
import { SkeletonText } from "../../ui/Skeleton";
import { toast } from "../../ui/Toast";
import { Alert, PageActions } from "../../ui/controls";
import Stepper from "../si-templates/Stepper";
import {
  EQUIPMENT_FIELDS,
  KIND_LABEL,
  applyProgram,
  approveProgram,
  fetchProgram,
  updateProgram,
  type EquipmentCard,
  type Plan,
  type Program,
} from "./api";
import ExampleCheckSheet from "./ExampleCheckSheet";
import PlanEditor from "./PlanEditor";

export const ATT_STEPS = [
  { key: "docs", label: "Документы", hint: "Пример протокола, программа, РЭ" },
  { key: "plan", label: "План от ассистента", hint: "Точки, режимы, характеристики, допуски" },
  { key: "check", label: "Сверка формул", hint: "Формулы на числах примера" },
  { key: "review", label: "Проверка метролога" },
  { key: "apply", label: "Применить к ИО" },
];

export default function ProgramView({
  id, isReviewer, canEdit, onBack, onChanged, onApplied,
}: {
  id: number;
  isReviewer: boolean;
  canEdit: boolean;
  onBack: () => void;
  onChanged: () => void;
  /** Заведена аттестация по шаблону — открыть её. */
  onApplied: (attestationId: number) => void;
}) {
  const [program, setProgram] = useState<Program | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const [checkOpen, setCheckOpen] = useState(false);

  const reset = (p: Program) => {
    setProgram(p);
    setPlan(p.plan ?? null);
    setTitle(p.title);
    setKind(p.equipment_kind);
    setNote(p.note);
  };

  const load = useCallback(async () => {
    try {
      reset(await fetchProgram(id));
      setLoadError("");
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, [id]);
  useEffect(() => void load(), [load]);

  const dirty = useMemo(
    () => !!program && (title !== program.title || kind !== program.equipment_kind || note !== program.note || JSON.stringify(plan) !== JSON.stringify(program.plan ?? null)),
    [program, title, kind, note, plan],
  );

  const save = async () => {
    if (!program || !plan || !dirty || busy) return;
    setBusy(true);
    setError("");
    try {
      const saved = await updateProgram(program.id, { title, equipment_kind: kind, note, plan });
      reset(saved);
      toast.success("Шаблон сохранён", { description: saved.needs_review ? "После правки шаблон снова проверяет метролог" : undefined });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
      toast.error("Не сохранилось", { description: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!program || busy) return;
    if (dirty) {
      setError("Сначала сохраните правки (⌘S) — проверяется сохранённый шаблон");
      return;
    }
    setBusy(true);
    try {
      reset(await approveProgram(program.id));
      toast.success("Шаблон проверен", { description: "Аттестации по нему начинаются с проверенным планом" });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async () => {
    if (!program) return;
    if (program.is_active && !(await confirmDialog({ title: "Выключить шаблон?", text: "Новые аттестации по нему заводить будет нельзя; проведённые не изменятся.", ok: "Выключить", danger: true }))) return;
    try {
      reset(await updateProgram(program.id, { is_active: !program.is_active }));
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useHotkey("mod+s", () => void save(), { allowInInputs: true, enabled: canEdit && !applyOpen && !checkOpen, description: "Сохранить шаблон аттестации", group: "Шаблоны аттестации" });

  if (!program || !plan) {
    return (
      <div className="att-page">
        {loadError ? (
          <Alert>Шаблон не открылся: {loadError}</Alert>
        ) : (
          <div className="card"><SkeletonText lines={6} /></div>
        )}
        <button type="button" className="link" onClick={onBack}>← Все шаблоны</button>
      </div>
    );
  }

  const check = "plan_check" in program ? program.plan_check ?? null : undefined;
  const checkOk = check ? check.ok && !check.stale : null;
  const step = program.needs_review ? 3 : 4;

  return (
    <div className="att-page att-program">
      <PageActions>
        {canEdit && (
          <button type="button" disabled={!dirty || busy} onClick={() => void save()}>
            {busy ? "Сохраняю…" : "Сохранить"} <Kbd keys="mod+s" className="kbd-hint" />
          </button>
        )}
        {isReviewer && program.needs_review && (
          <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>
            <Icon name="check" size={16} /> Проверен
          </button>
        )}
        {!program.needs_review && program.is_active && (
          <button type="button" className="primary" onClick={() => setApplyOpen(true)}>
            Применить к новому ИО <Icon name="arrowRight" size={16} />
          </button>
        )}
      </PageActions>

      <button type="button" className="link att-back" onClick={onBack}><Icon name="arrowLeft" size={15} /> Все шаблоны</button>
      <div className="att-program-head">
        <div className="grow">
          <input className="att-title-input" disabled={!canEdit} value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Название шаблона" />
          <div className="att-meta">
            <label className="sub">
              Вид ИО <input disabled={!canEdit} value={kind} onChange={(e) => setKind(e.target.value)} />
            </label>
            <span className={`pill ${program.needs_review ? "warn" : "ok"}`}>
              {program.needs_review ? "на проверке" : `проверен${program.reviewed_by ? ` · ${program.reviewed_by}` : ""}`}
            </span>
            {checkOk === false && <span className="pill bad">формулы не сходятся с примером</span>}
            {checkOk === true && <span className="pill ok">формулы сходятся</span>}
            {!program.is_active && <span className="pill muted">выключен</span>}
            {program.created_via_assistant && <span className="tag info"><Icon name="sparkle" size={11} /> собрал ассистент</span>}
            {program.used ? <span className="sub">применён {program.used} раз</span> : null}
            {dirty && <span className="pill muted">есть несохранённые правки</span>}
          </div>
        </div>
        <Stepper steps={ATT_STEPS} current={step} className="compact" />
      </div>

      {program.needs_review && (
        <Alert tone={isReviewer ? "info" : "warn"}>
          {isReviewer
            ? "Проверьте точки, режимы, формулы и допуски с источником, посмотрите сверку с примером — и отметьте «Проверен»."
            : "Шаблон ждёт проверки метролога. Аттестацию по нему завести можно, но план в ней тоже будет ждать проверки."}
        </Alert>
      )}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      <div className="card att-plan-card">
        <PlanEditor
          plan={plan}
          onChange={setPlan}
          readOnly={!canEdit}
          check={check}
          checkActions={canEdit ? (
            <button
              type="button" className="sm" disabled={dirty}
              title={dirty ? "Сначала сохраните план — сверяется сохранённый" : undefined}
              onClick={() => setCheckOpen(true)}
            >
              <Icon name="target" size={14} /> Сверить заново
            </button>
          ) : undefined}
          extraTabs={[
            {
              value: "sources",
              label: `Источники${program.sources.length ? ` · ${program.sources.length}` : ""}`,
              content: (
                <div className="att-sources">
                  {program.sources.length ? <ul>{program.sources.map((s, i) => <li key={i}>{s}</li>)}</ul> : <p className="sub">Источники не указаны.</p>}
                  <div className="field">
                    <label>Примечание</label>
                    <textarea disabled={!canEdit} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
                  </div>
                </div>
              ),
            },
          ]}
        />
      </div>

      <div className="att-program-foot">
        {program.needs_review && !program.is_active ? null : (
          <button type="button" className="sm" disabled={!program.is_active} onClick={() => setApplyOpen(true)}>
            <Icon name="plus" size={14} /> Аттестация нового ИО по шаблону
          </button>
        )}
        {canEdit && (
          <button type="button" className="sm link" onClick={() => void toggleActive()}>
            {program.is_active ? "Выключить шаблон" : "Включить шаблон"}
          </button>
        )}
      </div>

      <ExampleCheckSheet
        open={checkOpen}
        program={program}
        onClose={() => setCheckOpen(false)}
        onChecked={(p) => {
          setCheckOpen(false);
          reset(p);
          onChanged();
        }}
      />

      <ApplySheet
        open={applyOpen}
        program={program}
        onClose={() => setApplyOpen(false)}
        onApplied={(attId) => {
          setApplyOpen(false);
          onChanged();
          onApplied(attId);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
function ApplySheet({ open, program, onClose, onApplied }: { open: boolean; program: Program; onClose: () => void; onApplied: (id: number) => void }) {
  const [equipment, setEquipment] = useState<EquipmentCard>({ name: "", serial_number: "", inventory_number: "", manufacturer: "", year: "", owner: "", location: "" });
  const [kind, setKind] = useState("periodic");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) setEquipment((e) => ({ ...e, name: e.name || program.equipment_kind }));
  }, [open, program.equipment_kind]);

  const submit = async () => {
    if (busy) return;
    if (!equipment.name.trim() || !equipment.serial_number.trim()) {
      setError("Укажите наименование и заводской номер");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const att = await applyProgram(program.id, { equipment, kind });
      toast.success("Аттестация заведена по шаблону", { description: "Дальше — комиссия, средства измерений и измерения" });
      onApplied(att.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useHotkey("mod+enter", () => void submit(), { allowInInputs: true, enabled: open, global: true });

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Применить к новому ИО"
      subtitle={`План — из шаблона «${program.title}»${program.needs_review ? " (ждёт проверки — план аттестации тоже)" : ", проверен"}`}
      footer={
        <div className="si-step-actions">
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void submit()}>
            {busy ? "Завожу…" : "Завести аттестацию"} <Kbd keys="mod+enter" className="on-accent kbd-hint" />
          </button>
        </div>
      }
    >
      <div className="form-grid">
        <div className="field">
          <label>Вид аттестации</label>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {EQUIPMENT_FIELDS.map(([key, label], index) => (
          <div key={key} className="field">
            <label>{label}{key === "name" || key === "serial_number" ? " *" : ""}</label>
            <input autoFocus={index === 1} value={equipment[key]} onChange={(e) => setEquipment({ ...equipment, [key]: e.target.value })} />
          </div>
        ))}
      </div>
      <p className="sub">Комиссию, средства измерений и сами измерения внесёте в карточке аттестации. Измерения вносит только человек.</p>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
    </Sheet>
  );
}
