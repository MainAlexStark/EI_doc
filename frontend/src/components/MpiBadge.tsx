import { useState } from "react";
import { formatInterval, nextVerification, ruDate, shortParameter, type MpiRow } from "../mpi";

/**
 * МПИ и до какого числа действует поверка, сделанная сегодня (или в `from`). Ставится первым
 * в строке поиска типа — это то, что пишут в бумаге у клиента.
 */
export default function MpiBadge({
  mpi,
  from,
  compact,
}: {
  mpi: MpiRow[] | "loading" | "failed" | null | undefined;
  from?: Date;
  compact?: boolean;
}) {
  if (mpi === "loading") return <span className="mpi-badge muted">МПИ…</span>;
  if (mpi === "failed" || mpi == null) return null;
  const rows = mpi.filter((row) => row.months > 0);
  if (rows.length === 0) return <span className="mpi-badge muted">МПИ не указан</span>;
  // Одинаковые сроки по разным параметрам схлопываем: «6 лет» вместо «хол. 6 лет · гор. 6 лет».
  const unique = [...new Map(rows.map((r) => [r.months, r])).values()];
  return (
    <span className="mpi-badge" title="Межповерочный интервал и до какого числа действует поверка, сделанная сегодня">
      {unique.map((row, index) => (
        <span key={index} className="mpi-part">
          {unique.length > 1 && row.parameter && <span className="mpi-param">{shortParameter(row.parameter)} </span>}
          <b>{formatInterval(row.months)}</b>
          {!compact && <span className="mpi-date"> → до {ruDate(nextVerification(row.months, from))}</span>}
        </span>
      ))}
    </span>
  );
}

/** МПИ крупно и до какого числа действует поверка — с датой поверки, которую можно поменять. */
export function MpiHero({ mpi }: { mpi: MpiRow[] }) {
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const [day, setDay] = useState(today);
  const from = day ? new Date(`${day}T12:00:00`) : new Date();
  const rows = mpi.filter((row) => row.months > 0);
  if (!rows.length) return <div className="mpi-hero"><span className="mpi-badge muted">МПИ в реестре не указан</span></div>;
  return (
    <div className="mpi-hero">
      <div className="grow">
        {rows.map((row, index) => (
          <div key={index} className="mpi-badge">
            {row.parameter && <span className="mpi-param">{row.parameter}: </span>}
            <span>МПИ <b>{formatInterval(row.months)}</b></span>
            <span className="mpi-date">поверка действует до <b>{ruDate(nextVerification(row.months, from))}</b></span>
          </div>
        ))}
      </div>
      <label className="row" style={{ gap: 6, alignItems: "center", fontSize: 13 }}>
        <span className="muted">поверка</span>
        <input type="date" value={day} onChange={(event) => setDay(event.target.value)} />
      </label>
    </div>
  );
}

