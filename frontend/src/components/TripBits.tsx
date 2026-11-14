import { daysUntil, ruShort } from "./tripUtil";

/** Срок исполнения контракта: спокойно, пока далеко; жёлтым за две недели; красным после. */
export function DeadlinePill({ deadline, closed }: { deadline: string | null; closed?: boolean }) {
  const left = daysUntil(deadline);
  if (left == null || closed) return null;
  if (left < 0) return <span className="pill bad">срок истёк {ruShort(deadline)}</span>;
  if (left <= 14) return <span className="pill warn">до срока {left} дн.</span>;
  return <span className="pill muted plain">до {ruShort(deadline)}</span>;
}

export const plusHours = (time: string, hours: number) => {
  if (!time) return "";
  const [h, m] = time.split(":").map(Number);
  const total = Math.min(h * 60 + m + hours * 60, 23 * 60 + 59);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
};

/** Интервалы, которые обычно называют клиенту: по два часа, плюс «весь день» для выездов в районы. */
export function WindowPresets({
  from, till, workDay, onPick,
}: {
  from: string;
  till: string;
  workDay: [string, string];
  onPick: (from: string, till: string) => void;
}) {
  const startHour = Number(workDay[0].slice(0, 2)) || 8;
  const endHour = Number(workDay[1].slice(0, 2)) || 17;
  const slots: [string, string][] = [];
  for (let hour = startHour; hour + 2 <= Math.max(endHour, startHour + 2) + 1 && slots.length < 5; hour += 2) {
    slots.push([`${String(hour).padStart(2, "0")}:00`, `${String(hour + 2).padStart(2, "0")}:00`]);
  }
  const options: { label: string; value: [string, string] }[] = [
    ...slots.map((slot) => ({ label: `${Number(slot[0].slice(0, 2))}–${Number(slot[1].slice(0, 2))}`, value: slot })),
    { label: "Весь день", value: workDay },
    { label: "Без времени", value: ["", ""] },
  ];
  return (
    <div className="chip-row">
      {options.map((option) => {
        const active = option.value[0] === from && option.value[1] === till;
        return (
          <button
            type="button"
            key={option.label}
            className={`chip ${active ? "active" : ""}`}
            aria-pressed={active}
            onClick={() => onPick(option.value[0], option.value[1])}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
