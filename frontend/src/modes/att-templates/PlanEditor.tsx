/** План аттестации в понятном виде: общее, точки, режимы, характеристики с формулами и допусками,
 *  условия, порядок. Сырой JSON — отдельной вкладкой «для специалиста». Неизвестные поля плана
 *  (каркас протокола, заключение, preset…) сохраняются как есть.
 *  Используется в «Шаблонах аттестации» и в карточке аттестации ИО (components/Attestations.tsx). */

import { useEffect, useState, type ReactNode } from "react";
import Icon from "../../ui/Icon";
import { Segmented } from "../../ui/controls";
import { comma, toleranceText, type Characteristic, type Condition, type Mode, type Plan, type PlanCheck, type Point, type Tolerance } from "./api";

type Tab = "plan" | "check" | "json";

export default function PlanEditor({
  plan, onChange, readOnly, check, tolerances, extraTabs, checkActions,
}: {
  plan: Plan;
  onChange: (plan: Plan) => void;
  readOnly?: boolean;
  /** Отчёт сверки формул с примером; undefined — сервер его не отдал. */
  check?: PlanCheck | null;
  /** Допуски, как их посчитал сервер (у аттестации — plan_tolerances). */
  tolerances?: Record<string, string>;
  extraTabs?: { value: string; label: string; content: ReactNode }[];
  /** Кнопки под отчётом сверки (у шаблона — «Сверить заново»). */
  checkActions?: ReactNode;
}) {
  const [tab, setTab] = useState<string>("plan");
  const checkBad = !!check && (check.ok === false || check.stale);
  return (
    <div className="att-plan">
      <Segmented<string>
        size="sm"
        value={tab}
        onChange={setTab}
        options={[
          { value: "plan", label: "План", icon: "list" },
          { value: "check", label: checkBad ? "Сверка ⚠" : "Сверка с примером", icon: "target" },
          ...(extraTabs ?? []).map((t) => ({ value: t.value, label: t.label })),
          { value: "json", label: "Для специалиста", icon: "hash" },
        ]}
      />
      {tab === "plan" && <PlanTables plan={plan} onChange={onChange} readOnly={readOnly} tolerances={tolerances} />}
      {tab === "check" && <PlanCheckReport check={check} actions={checkActions} />}
      {tab === "json" && <PlanJson plan={plan} onChange={onChange} readOnly={readOnly} />}
      {extraTabs?.map((t) => tab === t.value && <div key={t.value}>{t.content}</div>)}
    </div>
  );
}

// ---------------------------------------------------------------------------
const lines = (v: string) => v.split("\n").map((s) => s.trim()).filter(Boolean);
const csv = (v: string) => v.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
const paramsText = (p: Record<string, string> | undefined) => Object.entries(p ?? {}).map(([k, v]) => `${k}=${v}`).join("; ");
const parseParams = (v: string) =>
  Object.fromEntries(v.split(/[;\n]/).map((s) => s.split("=").map((x) => x.trim())).filter((x) => x[0] && x.length >= 2).map(([k, ...rest]) => [k, rest.join("=")]));

function nextKey(prefix: string, used: string[]) {
  let i = used.length + 1;
  while (used.includes(`${prefix}${i}`)) i += 1;
  return `${prefix}${i}`;
}

export function PlanTables({
  plan, onChange, readOnly, tolerances,
}: {
  plan: Plan;
  onChange: (plan: Plan) => void;
  readOnly?: boolean;
  tolerances?: Record<string, string>;
}) {
  const ro = !!readOnly;
  const set = <K extends keyof Plan>(key: K, value: Plan[K]) => onChange({ ...plan, [key]: value });
  const setRow = <T,>(key: "points" | "modes" | "characteristics" | "conditions", index: number, patch: Partial<T>) =>
    onChange({ ...plan, [key]: (plan[key] as T[]).map((row, i) => (i === index ? { ...row, ...patch } : row)) });
  const dropRow = (key: "points" | "modes" | "characteristics" | "conditions", index: number) =>
    onChange({ ...plan, [key]: (plan[key] as unknown[]).filter((_, i) => i !== index) });
  const points = plan.points ?? [];
  const modes = plan.modes ?? [];
  const chars = plan.characteristics ?? [];
  const conds = plan.conditions ?? [];
  const groups = Array.from(new Set(["all", "volume", "control", ...points.flatMap((p) => p.groups ?? [])]));

  return (
    <div className="att-tables">
      <section>
        <h4>Общее</h4>
        <div className="form-grid att-general">
          <Field label="Вид оборудования"><input disabled={ro} value={plan.equipment_kind ?? ""} onChange={(e) => set("equipment_kind", e.target.value)} /></Field>
          <Field label="Величина"><input disabled={ro} value={plan.quantity ?? ""} onChange={(e) => set("quantity", e.target.value)} placeholder="температура" /></Field>
          <Field label="Единица"><input disabled={ro} value={plan.unit ?? ""} onChange={(e) => set("unit", e.target.value)} placeholder="°С" /></Field>
          <Field label="Измерений в точке">
            <input disabled={ro} type="number" min={1} max={100} value={plan.repeats ?? 1} onChange={(e) => set("repeats", Math.max(1, Number(e.target.value) || 1))} />
          </Field>
          <Field label="Программа и методика" wide><input disabled={ro} value={plan.program ?? ""} onChange={(e) => set("program", e.target.value)} /></Field>
          <Field label="Назначение (в заключение)" wide><input disabled={ro} value={plan.purpose ?? ""} onChange={(e) => set("purpose", e.target.value)} /></Field>
          <Field label="Периодичность"><input disabled={ro} value={plan.periodicity ?? ""} onChange={(e) => set("periodicity", e.target.value)} placeholder="один раз в год" /></Field>
          <Field label="Документы (по строке)" wide>
            <textarea disabled={ro} rows={2} value={(plan.documents ?? []).join("\n")} onChange={(e) => set("documents", lines(e.target.value))} />
          </Field>
        </div>
      </section>

      <section>
        <h4>Точки измерений <span className="sub">{points.length}</span></h4>
        <div className="table-wrap">
          <table className="compact att-edit">
            <thead><tr><th>Ключ</th><th>Где</th><th>Роль</th><th>Группы (через запятую)</th><th /></tr></thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={i} className={p.role === "control" ? "control" : ""}>
                  <td className="k"><input className="data" disabled={ro} value={p.key} onChange={(e) => setRow<Point>("points", i, { key: e.target.value })} /></td>
                  <td><input disabled={ro} value={p.label} onChange={(e) => setRow<Point>("points", i, { label: e.target.value })} placeholder="верх, угол 1" /></td>
                  <td>
                    <select disabled={ro} value={p.role ?? "volume"} onChange={(e) => setRow<Point>("points", i, { role: e.target.value as Point["role"] })}>
                      <option value="volume">в объёме</option>
                      <option value="control">контрольная</option>
                    </select>
                  </td>
                  <td><input disabled={ro} value={(p.groups ?? []).join(", ")} onChange={(e) => setRow<Point>("points", i, { groups: csv(e.target.value) })} placeholder="corners" /></td>
                  <td>{!ro && <DropBtn onClick={() => dropRow("points", i)} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!ro && <AddBtn label="Точка" onClick={() => set("points", [...points, { key: nextKey("P", points.map((p) => p.key)), label: "", role: "volume", groups: [] }])} />}
      </section>

      <section>
        <h4>Режимы <span className="sub">{modes.length}</span></h4>
        <div className="table-wrap">
          <table className="compact att-edit">
            <thead><tr><th>Ключ</th><th>Название</th><th>Задано{plan.unit ? `, ${plan.unit}` : ""}</th><th>Параметры режима (имя=число; …)</th><th /></tr></thead>
            <tbody>
              {modes.map((m, i) => (
                <tr key={i}>
                  <td className="k"><input className="data" disabled={ro} value={m.key} onChange={(e) => setRow<Mode>("modes", i, { key: e.target.value })} /></td>
                  <td><input disabled={ro} value={m.label} onChange={(e) => setRow<Mode>("modes", i, { label: e.target.value })} placeholder="+5 °С" /></td>
                  <td className="num"><input className="data" inputMode="decimal" disabled={ro} value={m.setpoint} onChange={(e) => setRow<Mode>("modes", i, { setpoint: e.target.value.replace(",", ".") })} /></td>
                  <td><ParamsInput disabled={ro} value={m.params} onChange={(params) => setRow<Mode>("modes", i, { params })} /></td>
                  <td>{!ro && <DropBtn onClick={() => dropRow("modes", i)} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!ro && <AddBtn label="Режим" onClick={() => set("modes", [...modes, { key: nextKey("m", modes.map((m) => m.key)), label: "", setpoint: "", params: {} }])} />}
      </section>

      <section>
        <h4>Характеристики <span className="sub">формулы считает EI_doc; допуск — с источником</span></h4>
        <div className="att-chars">
          {chars.map((c, i) => (
            <div key={i} className="att-char">
              <div className="att-char-row">
                <input className="att-char-label" disabled={ro} value={c.label} onChange={(e) => setRow<Characteristic>("characteristics", i, { label: e.target.value })} placeholder="Отклонение температуры" />
                <input className="data att-char-key" disabled={ro} value={c.key} title="Ключ — по нему характеристика доступна в формулах ниже" onChange={(e) => setRow<Characteristic>("characteristics", i, { key: e.target.value })} />
                <select disabled={ro} value={c.per ?? "mode"} onChange={(e) => setRow<Characteristic>("characteristics", i, { per: e.target.value as Characteristic["per"] })}>
                  <option value="mode">на режим</option>
                  <option value="point">по точкам</option>
                </select>
                {c.per === "point" && (
                  <select disabled={ro} value={c.group ?? "all"} onChange={(e) => setRow<Characteristic>("characteristics", i, { group: e.target.value })} title="Группа точек">
                    {groups.map((g) => <option key={g} value={g}>{g}</option>)}
                  </select>
                )}
                {!ro && <DropBtn onClick={() => dropRow("characteristics", i)} />}
              </div>
              <div className="att-char-grid">
                <Field label="Формула (выражение)">
                  <input className="data" disabled={ro} value={c.expr} onChange={(e) => setRow<Characteristic>("characteristics", i, { expr: e.target.value })} placeholder="mean(all) - setpoint" />
                </Field>
                <Field label="Как в протоколе">
                  <input disabled={ro} value={c.text ?? ""} onChange={(e) => setRow<Characteristic>("characteristics", i, { text: e.target.value })} placeholder="Δ = T̄ − Tзад" />
                </Field>
                <ToleranceInput disabled={ro} value={c.tolerance} onChange={(tolerance) => setRow<Characteristic>("characteristics", i, { tolerance })} />
                <Field label="Знаков">
                  <input className="data" type="number" min={0} max={6} disabled={ro} value={c.decimals ?? ""} onChange={(e) => setRow<Characteristic>("characteristics", i, { decimals: e.target.value === "" ? undefined : Number(e.target.value) })} />
                </Field>
                <Field label="Источник допуска" wide>
                  <input disabled={ro} value={c.source ?? ""} onChange={(e) => setRow<Characteristic>("characteristics", i, { source: e.target.value })} placeholder="программа аттестации, п. 7.3" className={!c.source && c.tolerance?.kind !== "none" ? "att-missing" : ""} />
                </Field>
              </div>
              <div className="sub att-char-foot">
                Допуск: {tolerances?.[c.key] ?? toleranceText(c.tolerance)}
                {!c.source && c.tolerance && c.tolerance.kind !== "none" && <span className="fail-text"> · без источника сервер не примет</span>}
              </div>
            </div>
          ))}
        </div>
        {!ro && (
          <AddBtn
            label="Характеристика"
            onClick={() => set("characteristics", [...chars, { key: nextKey("c", chars.map((c) => c.key)), label: "", expr: "", text: "", per: "mode", group: "all", tolerance: { kind: "none" }, source: "" }])}
          />
        )}
        <p className="hint">
          В формулах: группы точек (<code>all</code>, <code>volume</code>, <code>control</code>, свои), <code>setpoint</code>, <code>display</code>, параметры режима,
          ключи характеристик выше; функции <code>mean min max sum range std means abs sqrt round point("A")</code>.
        </p>
      </section>

      <section>
        <h4>Условия проведения <span className="sub">их вносит человек при аттестации</span></h4>
        <div className="table-wrap">
          <table className="compact att-edit">
            <thead><tr><th>Ключ</th><th>Что</th><th>Единица</th><th /></tr></thead>
            <tbody>
              {conds.map((c, i) => (
                <tr key={i}>
                  <td className="k"><input className="data" disabled={ro} value={c.key} onChange={(e) => setRow<Condition>("conditions", i, { key: e.target.value })} /></td>
                  <td><input disabled={ro} value={c.label} onChange={(e) => setRow<Condition>("conditions", i, { label: e.target.value })} placeholder="Температура окружающего воздуха" /></td>
                  <td><input disabled={ro} value={c.unit ?? ""} onChange={(e) => setRow<Condition>("conditions", i, { unit: e.target.value })} /></td>
                  <td>{!ro && <DropBtn onClick={() => dropRow("conditions", i)} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!ro && <AddBtn label="Условие" onClick={() => set("conditions", [...conds, { key: nextKey("cond", conds.map((c) => c.key)), label: "", unit: "" }])} />}
      </section>

      <section>
        <h4>Порядок проведения</h4>
        <textarea disabled={ro} rows={3} value={(plan.procedure ?? []).join("\n")} onChange={(e) => set("procedure", lines(e.target.value))} placeholder="по пункту на строку" />
        {Array.isArray(plan.layout) && plan.layout.length > 0 && <p className="sub">Вид протокола — по каркасу из примера ({plan.layout.length} блоков, правится во вкладке «Для специалиста»).</p>}
      </section>
    </div>
  );
}

function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`field ${wide ? "wide" : ""}`}>
      <label>{label}</label>
      {children}
    </div>
  );
}
const AddBtn = ({ label, onClick }: { label: string; onClick: () => void }) => (
  <button type="button" className="sm att-add" onClick={onClick}><Icon name="plus" size={14} /> {label}</button>
);
const DropBtn = ({ onClick }: { onClick: () => void }) => (
  <button type="button" className="icon-btn" aria-label="Убрать" title="Убрать" onClick={onClick}><Icon name="x" size={14} /></button>
);

/** Параметры режима — строкой «tol=2; rate=0,5»: правится как текст, разбирается при выходе из поля. */
function ParamsInput({ value, onChange, disabled }: { value?: Record<string, string>; onChange: (v: Record<string, string>) => void; disabled?: boolean }) {
  const [text, setText] = useState(paramsText(value));
  useEffect(() => setText(paramsText(value)), [value]);
  return <input className="data" disabled={disabled} value={text} onChange={(e) => setText(e.target.value)} onBlur={() => onChange(parseParams(text.replace(/,(?=\d)/g, ".")))} placeholder="tol=2" />;
}

function ToleranceInput({ value, onChange, disabled }: { value?: Tolerance; onChange: (t: Tolerance) => void; disabled?: boolean }) {
  const t = value ?? { kind: "none" as const };
  return (
    <div className="field att-tol">
      <label>Допуск</label>
      <div className="att-tol-row">
        <select disabled={disabled} value={t.kind} onChange={(e) => onChange({ kind: e.target.value as Tolerance["kind"] })}>
          <option value="none">нет</option>
          <option value="abs">± предел</option>
          <option value="range">от … до</option>
        </select>
        {t.kind === "abs" && <input className="data" disabled={disabled} value={t.limit ?? ""} onChange={(e) => onChange({ ...t, limit: e.target.value })} placeholder="tol" />}
        {t.kind === "range" && (
          <>
            <input className="data" disabled={disabled} value={t.min ?? ""} onChange={(e) => onChange({ ...t, min: e.target.value })} placeholder="setpoint - tol" />
            <input className="data" disabled={disabled} value={t.max ?? ""} onChange={(e) => onChange({ ...t, max: e.target.value })} placeholder="setpoint + tol" />
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function PlanJson({ plan, onChange, readOnly }: { plan: Plan; onChange: (plan: Plan) => void; readOnly?: boolean }) {
  const [raw, setRaw] = useState(() => JSON.stringify(plan, null, 2));
  const [error, setError] = useState("");
  useEffect(() => setRaw(JSON.stringify(plan, null, 2)), [plan]);
  return (
    <div className="att-json">
      <p className="sub">План целиком — как его хранит EI_doc. Правка здесь меняет и таблицы; сервер проверит план при сохранении.</p>
      <textarea
        className="code data"
        rows={22}
        spellCheck={false}
        readOnly={readOnly}
        value={raw}
        onChange={(e) => {
          setRaw(e.target.value);
          try {
            const parsed = JSON.parse(e.target.value);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
            setError("");
            onChange(parsed as Plan);
          } catch {
            setError("Это пока не JSON-объект — проверьте скобки и кавычки; изменения не применены");
          }
        }}
      />
      {error && <div className="error">{error}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
/** Отчёт сверки формул с примером: «в примере / посчитано / сходится», расхождения красным. */
export function PlanCheckReport({ check, compact, actions }: { check?: PlanCheck | null; compact?: boolean; actions?: ReactNode }) {
  const foot = actions ? <div className="att-check-actions">{actions}</div> : null;
  if (check === undefined) {
    return (
      <div className="att-check empty">
        <Icon name="info" size={16} />
        <span>Отчёт сверки для шаблона сервер пока не отдаёт — он виден в аттестации, заведённой по шаблону, и в чате ассистента.</span>
      </div>
    );
  }
  if (!check || !check.rows || check.rows.length === 0) {
    return (
      <div className="att-check empty">
        <Icon name="info" size={16} />
        <span>Сверки с примером нет — в документах не нашлось отсчётов с результатами. Метролог сверяет формулы с методикой.</span>
        {foot}
      </div>
    );
  }
  const bad = check.rows.filter((r) => !r.ok).length;
  return (
    <div className={`att-check ${compact ? "compact" : ""}`}>
      <div className="att-check-head">
        {check.stale ? (
          <span className="pill warn">план менялся после сверки</span>
        ) : check.ok ? (
          <span className="pill ok">всё сходится · {check.rows.length}</span>
        ) : (
          <span className="pill bad">расхождений: {bad} из {check.rows.length}</span>
        )}
        {check.source && <span className="sub">пример: {check.source}</span>}
        {check.checked_at && <span className="sub">сверено {new Date(check.checked_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })}{check.checked_by ? ` · ${check.checked_by}` : ""}</span>}
      </div>
      <div className="table-wrap">
        <table className="compact att-check-table">
          <thead><tr><th>Режим</th><th>Характеристика</th><th className="num">В примере</th><th className="num">Посчитано</th><th>Сходится</th></tr></thead>
          <tbody>
            {check.rows.map((r, i) => (
              <tr key={i} className={r.ok ? "" : "bad"}>
                <td>{r.mode}</td>
                <td>{r.label}{r.point && <span className="sub">, точка {r.point}</span>}</td>
                <td className="num data">{comma(r.expected)}</td>
                <td className="num data">{comma(r.computed)}</td>
                <td>{r.ok ? <span className="pass-text"><Icon name="check" size={14} /> да</span> : <span className="fail-text"><Icon name="x" size={14} /> нет</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(check.errors ?? []).length > 0 && <ul className="problems">{check.errors!.map((e) => <li key={e}>{e}</li>)}</ul>}
      {(check.unchecked ?? []).length > 0 && <p className="sub">Не с чем сверить: {check.unchecked!.join(", ")}</p>}
      {foot}
    </div>
  );
}

export type { Tab as PlanTab };
