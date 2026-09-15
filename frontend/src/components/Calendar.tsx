import { useMemo, useState } from "react";

export type CalendarMarker = { priority?: boolean };

type MonthCalendarProps = {
  /** Выбранные даты (ISO yyyy-mm-dd) — один элемент для одиночного выбора, несколько для мультивыбора. */
  selected: string[];
  onToggle: (date: string) => void;
  /** Даты, которые нужно как-то выделить (есть слот доступности и т. п.). */
  markers?: Record<string, CalendarMarker>;
  /** Кликабельны только даты из markers — остальные показаны, но недоступны. */
  restrictToMarkers?: boolean;
  /** Раньше этой даты (ISO) дни недоступны. По умолчанию — сегодня. */
  minDate?: string;
};

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function toIso(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

const TODAY = toIso(new Date());

/** Месячная сетка с мультивыбором дней — общий календарь для графика сотрудника и формы заявки. */
export default function MonthCalendar({
  selected,
  onToggle,
  markers = {},
  restrictToMarkers = false,
  minDate = TODAY,
}: MonthCalendarProps) {
  const initial = useMemo(() => {
    const base = selected[0] ? new Date(selected[0]) : new Date(minDate);
    return new Date(base.getFullYear(), base.getMonth(), 1);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [cursor, setCursor] = useState(initial);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const cells = useMemo(() => {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const first = new Date(year, month, 1);
    const offset = (first.getDay() + 6) % 7; // понедельник — первый день недели
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const list: (string | null)[] = [];
    for (let i = 0; i < offset; i += 1) list.push(null);
    for (let day = 1; day <= daysInMonth; day += 1) list.push(toIso(new Date(year, month, day)));
    return list;
  }, [cursor]);

  const monthLabel = cursor.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });

  return (
    <div className="calendar">
      <div className="calendar-head">
        <button
          type="button"
          onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
          aria-label="предыдущий месяц"
        >
          ‹
        </button>
        <span className="calendar-month">{monthLabel}</span>
        <button
          type="button"
          onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
          aria-label="следующий месяц"
        >
          ›
        </button>
      </div>
      <div className="calendar-grid calendar-weekdays">
        {WEEKDAYS.map((label) => (
          <span key={label} className="calendar-weekday">
            {label}
          </span>
        ))}
      </div>
      <div className="calendar-grid">
        {cells.map((date, index) => {
          if (!date) return <span key={`empty-${index}`} className="calendar-cell empty" />;
          const marker = markers[date];
          const isPast = date < minDate;
          const isMarked = Boolean(marker);
          const clickable = !isPast && (!restrictToMarkers || isMarked);
          const isSelected = selectedSet.has(date);
          const classes = ["calendar-cell"];
          if (isSelected) classes.push("selected");
          if (!clickable) classes.push("disabled");
          if (isMarked) classes.push("marked");
          if (date === TODAY) classes.push("today");
          return (
            <button
              type="button"
              key={date}
              className={classes.join(" ")}
              disabled={!clickable}
              onClick={() => onToggle(date)}
            >
              {Number(date.slice(8, 10))}
              {marker?.priority && <span className="calendar-dot" title="приоритетное время" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
