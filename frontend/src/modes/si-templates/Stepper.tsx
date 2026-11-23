/** Шаги потока: 1 → 2 → 3 → 4. Пройденные — с галочкой и кликабельны (вернуться), текущий — акцентом.
 *  Используется и в «Шаблонах аттестации». Стили — si-templates.css (.flow-steps). */

import Icon from "../../ui/Icon";

export type FlowStep = { key: string; label: string; hint?: string };

export default function Stepper({
  steps, current, onPick, className,
}: {
  steps: FlowStep[];
  /** Индекс текущего шага (0…). */
  current: number;
  /** Вернуться на пройденный шаг; без обработчика шаги не кликаются. */
  onPick?: (index: number) => void;
  className?: string;
}) {
  return (
    <ol className={`flow-steps ${className ?? ""}`} aria-label="Шаги">
      {steps.map((step, index) => {
        const state = index < current ? "done" : index === current ? "current" : "next";
        const clickable = !!onPick && index < current;
        return (
          <li key={step.key} className={`flow-step ${state}`} aria-current={state === "current" ? "step" : undefined}>
            <button type="button" disabled={!clickable} onClick={() => clickable && onPick?.(index)} title={step.hint}>
              <span className="flow-num">{state === "done" ? <Icon name="check" size={13} strokeWidth={2.6} /> : index + 1}</span>
              <span className="flow-label">{step.label}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
