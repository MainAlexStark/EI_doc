import { useEffect, useMemo, useState } from "react";
import Icon from "../ui/Icon";

export type CalendarMarker = { priority?: boolean };

type MonthCalendarProps = {
  /** Выбранные даты (ISO yyyy-mm-dd) — один элемент для одиночного выбора, несколько для мультивыбора. */
  selected: string[];
  onToggle: (date: string) => void;
  /** Даты, которые нужно как-то выделить (есть слот доступности и т. п.). */
  markers?: Record<string, CalendarMarker>;
  /** Кликабельны только даты из markers — остальные показаны, но недоступны. */
  restrictToMarkers?: boolean;
  /** Раньше этой даты (ISO) дни недоступны. По умолчанию — сегодня; "" — без ограничения. */
  minDate?: string;
  /** Показываемый месяц снаружи (навигация «Графика» двигает его вместе с большим календарём). */
  month?: Date;
  /** Подсветка периода (видимая неделя в «Графике»). */
  range?: string[];
  /** Дни соседних месяцев в сетке. */
  showOutside?: boolean;
  className?: string;
};

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function toIso(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

const fromIso = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};

/** Месячная сетка с мультивыбором дней — для формы заявки и как мини-календарь «Графика». */
export default function MonthCalendar({
  selected,
  onToggle,
  markers = {},
  restrictToMarkers = false,
  minDate,
  month,
  range,
  showOutside = false,
  className,
}: MonthCalendarProps) {
  const today = toIso(new Date());
  const min = minDate ?? today;
  const initial = useMemo(() => {
    const base = month ?? (selected[0] ? fromIso(selected[0]) : min ? fromIso(min) : new Date());
    return new Date(base.getFullYear(), base.getMonth(), 1);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [cursor, setCursor] = useState(initial);

  useEffect(() => {
    if (month) setCursor(new Date(month.getFullYear(), month.getMonth(), 1));
  }, [month?.getFullYear(), month?.getMonth()]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const rangeSet = useMemo(() => new Set(range ?? []), [range]);

  const cells = useMemo(() => {
    const year = cursor.getFullYear();
    const m = cursor.getMonth();
    const first = new Date(year, m, 1);
    const offset = (first.getDay() + 6) % 7; // понедельник — первый день недели
    const daysInMonth = new Date(year, m + 1, 0).getDate();
    const list: { iso: string; outside: boolean }[] = [];
    for (let i = offset; i > 0; i -= 1) list.push({ iso: toIso(new Date(year, m, 1 - i)), outside: true });
    for (let day = 1; day <= daysInMonth; day += 1) list.push({ iso: toIso(new Date(year, m, day)), outside: false });
    while (list.length % 7 !== 0) {
      const last = fromIso(list[list.length - 1].iso);
      last.setDate(last.getDate() + 1);
      list.push({ iso: toIso(last), outside: true });
    }
    return list;
  }, [cursor]);

  const monthLabel = cursor.toLocaleDateString("ru-RU", { month: "long", year: "numeric" }).replace(" г.", "");

  return (
    <div className={`calendar ${className ?? ""}`}>
      <div className="calendar-head">
        <span className="calendar-month">{monthLabel}</span>
        <span className="btn-group">
          <button
            type="button"
            className="icon-btn sm"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
            aria-label="Предыдущий месяц"
          >
            <Icon name="chevronLeft" size={16} />
          </button>
          <button
            type="button"
            className="icon-btn sm"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
            aria-label="Следующий месяц"
          >
            <Icon name="chevronRight" size={16} />
          </button>
        </span>
      </div>
      <div className="calendar-grid calendar-weekdays">
        {WEEKDAYS.map((label) => (
          <span key={label} className="calendar-weekday">
            {label}
          </span>
        ))}
      </div>
      <div className="calendar-grid">
        {cells.map(({ iso, outside }) => {
          if (outside && !showOutside) return <span key={iso} className="calendar-cell empty" />;
          const marker = markers[iso];
          const isPast = Boolean(min) && iso < min;
          const isMarked = Boolean(marker);
          const clickable = !isPast && (!restrictToMarkers || isMarked);
          const isSelected = selectedSet.has(iso);
          const classes = ["calendar-cell"];
          if (isSelected) classes.push("selected");
          if (!clickable) classes.push("disabled");
          if (isMarked && !isSelected) classes.push("marked");
          if (iso === today) classes.push("today");
          if (outside) classes.push("outside");
          if (rangeSet.has(iso) && !isSelected) classes.push("in-range");
          return (
            <button
              type="button"
              key={iso}
              className={classes.join(" ")}
              disabled={!clickable}
              aria-pressed={isSelected}
              aria-label={fromIso(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}
              onClick={() => onToggle(iso)}
            >
              {Number(iso.slice(8, 10))}
              {marker?.priority && <span className="calendar-dot" title="приоритетное время" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
