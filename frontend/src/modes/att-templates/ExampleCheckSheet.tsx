/** «Сверить заново»: числа примера протокола (отсчёты, показание, напечатанные результаты) →
 *  POST /api/attestations/programs/{id}/check/. Числа уходят только на сверку — сервер хранит
 *  лишь отчёт («в примере / посчитано / сходится»), не измерения. */

import { useEffect, useMemo, useState } from "react";
import Kbd from "../../ui/Kbd";
import { Sheet } from "../../ui/Sheet";
import { useHotkey } from "../../ui/Hotkeys";
import { toast } from "../../ui/Toast";
import { Alert } from "../../ui/controls";
import { checkProgram, type CheckExample, type Plan, type Point, type Program } from "./api";

const groupPoints = (plan: Plan, group?: string): Point[] =>
  !group || group === "all" ? plan.points : plan.points.filter((p) => p.role === group || p.groups?.includes(group));

const nums = (v: string) => v.split(/[\s;]+/).map((s) => s.trim()).filter(Boolean);

type ModeForm = { display: string; readings: Record<string, string>; expected: Record<string, string> };

export default function ExampleCheckSheet({
  open, program, onClose, onChecked,
}: {
  open: boolean;
  program: Program;
  onClose: () => void;
  onChecked: (p: Program) => void;
}) {
  const plan = program.plan!;
  const [source, setSource] = useState("");
  const [modeKey, setModeKey] = useState(plan.modes[0]?.key ?? "");
  const [forms, setForms] = useState<Record<string, ModeForm>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) setSource((s) => s || program.plan_check?.source || "");
  }, [open, program.plan_check?.source]);

  const form: ModeForm = forms[modeKey] ?? { display: "", readings: {}, expected: {} };
  const patch = (p: Partial<ModeForm>) => setForms({ ...forms, [modeKey]: { ...form, ...p } });
  const usesDisplay = useMemo(() => plan.characteristics.some((c) => /\bdisplay\b/.test(c.expr ?? "")), [plan]);

  const build = (): CheckExample => {
    const modes: CheckExample["modes"] = {};
    for (const [key, f] of Object.entries(forms)) {
      const readings = Object.fromEntries(Object.entries(f.readings).map(([p, v]) => [p, nums(v)]).filter(([, v]) => v.length));
      const expected: Record<string, string | Record<string, string>> = {};
      for (const [k, v] of Object.entries(f.expected)) {
        if (!v.trim()) continue;
        const [ck, pk] = k.split("::");
        if (pk) expected[ck] = { ...((expected[ck] as Record<string, string>) ?? {}), [pk]: v.trim() };
        else expected[ck] = v.trim();
      }
      if (!Object.keys(readings).length) continue;
      modes[key] = { ...(f.display.trim() ? { display: f.display.trim() } : {}), readings, expected };
    }
    return { source: source.trim(), modes };
  };

  const submit = async () => {
    if (busy) return;
    const example = build();
    if (!Object.keys(example.modes).length) {
      setError("Внесите отсчёты примера хотя бы по одному режиму");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const p = await checkProgram(program.id, example);
      const c = p.plan_check;
      if (c?.ok) toast.success("Формулы сходятся с примером");
      else toast.warn("Есть расхождения с примером", { description: "Смотрите отчёт сверки" });
      onChecked(p);
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
      size="lg"
      title="Сверить формулы с примером"
      subtitle="Числа из примера протокола уходят только на сверку — измерениями они не сохраняются"
      footer={
        <div className="si-step-actions">
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void submit()}>
            {busy ? "Сверяю…" : "Сверить"} <Kbd keys="mod+enter" className="on-accent kbd-hint" />
          </button>
        </div>
      }
    >
      <div className="form-grid">
        <div className="field">
          <label>Пример (источник)</label>
          <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="протокол 1886/2026" />
        </div>
        <div className="field">
          <label>Режим</label>
          <select value={modeKey} onChange={(e) => setModeKey(e.target.value)}>
            {plan.modes.map((m) => <option key={m.key} value={m.key}>{m.label || m.key}{forms[m.key] ? " ●" : ""}</option>)}
          </select>
        </div>
        {usesDisplay && (
          <div className="field">
            <label>Показание индикатора ИО</label>
            <input inputMode="decimal" value={form.display} onChange={(e) => patch({ display: e.target.value })} />
          </div>
        )}
      </div>
      <h4>Отсчёты · {plan.repeats} на точку, через пробел</h4>
      <div className="table-wrap">
        <table className="compact att-edit">
          <tbody>
            {plan.points.map((p) => (
              <tr key={p.key}>
                <td>{p.label || p.key}</td>
                <td className="grow">
                  <input
                    className="data" value={form.readings[p.key] ?? ""}
                    onChange={(e) => patch({ readings: { ...form.readings, [p.key]: e.target.value } })}
                  />
                </td>
                <td className="sub num">{nums(form.readings[p.key] ?? "").length}/{plan.repeats}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>Результаты, как напечатаны в примере</h4>
      <div className="table-wrap">
        <table className="compact att-edit">
          <tbody>
            {plan.characteristics.flatMap((c) =>
              (c.per === "point" ? groupPoints(plan, c.group).map((p) => [`${c.key}::${p.key}`, `${c.label}, точка ${p.label || p.key}`]) : [[c.key, c.label]]).map(([k, label]) => (
                <tr key={k}>
                  <td>{label}</td>
                  <td className="grow">
                    <input className="data" inputMode="decimal" value={form.expected[k] ?? ""} onChange={(e) => patch({ expected: { ...form.expected, [k]: e.target.value } })} />
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      <p className="sub">Пустые результаты не сверяются. Совпадение — в пределах единицы последнего напечатанного знака.</p>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
    </Sheet>
  );
}
