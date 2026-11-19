import { useState } from "react";
import Icon from "../../ui/Icon";

/** Быстрое назначение: день (и время), потом одно касание по человеку — и готово.
 *
 * Один и тот же блок на «Панели», карте «Выездов», в «Приёмках» и командном
 * «Графике»: руководитель привыкает к одному жесту. */

export type PickPerson = { id: number; name: string; color?: string; hint?: string; muted?: boolean };

export default function AssignPicker({
  people, currentId, date, withDate = true, withTime = false, busy, onPick, compact,
}: {
  people: PickPerson[];
  currentId?: number | null;
  date?: string | null;
  withDate?: boolean;
  withTime?: boolean;
  busy?: boolean;
  compact?: boolean;
  onPick: (employeeId: number, date: string | null, time: string | null) => void;
}) {
  const [day, setDay] = useState(date ?? "");
  const [time, setTime] = useState("");
  return (
    <div className={`assign-picker ${compact ? "compact" : ""}`}>
      {(withDate || withTime) && (
        <div className="assign-when">
          {withDate && (
            <label>
              <span>День</span>
              <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
            </label>
          )}
          {withTime && (
            <label>
              <span>С</span>
              <input type="time" step={1800} value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
          )}
        </div>
      )}
      <div className="assign-people" role="listbox" aria-label="Кому">
        {people.map((p) => (
          <button
            type="button"
            key={p.id}
            role="option"
            aria-selected={p.id === currentId}
            className={`assign-person ${p.id === currentId ? "current" : ""} ${p.muted ? "muted" : ""}`}
            disabled={busy}
            onClick={() => onPick(p.id, day || null, time || null)}
            title={p.hint}
          >
            <span className="dot" style={{ background: p.color ?? "var(--faint)" }} />
            <span className="who">{p.name}</span>
            {p.hint && <span className="hint">{p.hint}</span>}
            {p.id === currentId && <Icon name="check" size={14} />}
          </button>
        ))}
      </div>
    </div>
  );
}
