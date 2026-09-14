import { useCallback, useEffect, useState } from "react";
import {
  downloadJournal,
  fetchJournal,
  type JournalFilters,
  type JournalRow,
} from "../api";

const PAGE_SIZE = 50;

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "—";

function StatusPill({ row }: { row: JournalRow }) {
  if (row.needs_review) return <span className="pill warn">на сверку</span>;
  if (row.protocol_status === "signed") return <span className="pill ok">подписан</span>;
  if (row.protocol_status === "published") return <span className="pill ok">в ФИФ</span>;
  if (row.protocol_status === "void") return <span className="pill bad">аннулирован</span>;
  if (row.protocol_status === "numbered") return <span className="pill muted">номер присвоен</span>;
  return <span className="pill muted">черновик</span>;
}

export default function Journal() {
  const [filters, setFilters] = useState<JournalFilters>({ page: 1 });
  const [rows, setRows] = useState<JournalRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (active: JournalFilters) => {
    setLoading(true);
    setError("");
    try {
      const page = await fetchJournal(active);
      setRows(page.results);
      setTotal(page.count);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить журнал");
    } finally {
      setLoading(false);
    }
  }, []);

  // Поиск не дёргает сервер на каждую букву.
  useEffect(() => {
    const timer = setTimeout(() => void load(filters), filters.q ? 300 : 0);
    return () => clearTimeout(timer);
  }, [filters, load]);

  const update = (patch: Partial<JournalFilters>) =>
    setFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));

  const page = filters.page ?? 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      <div className="filters">
        <div className="field">
          <label htmlFor="journal-q">Поиск</label>
          <input
            id="journal-q"
            style={{ width: 260 }}
            placeholder="заводской номер, тип СИ, адрес"
            value={filters.q ?? ""}
            onChange={(event) => update({ q: event.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="journal-from">Поверено с</label>
          <input
            id="journal-from"
            type="date"
            value={filters.date_from ?? ""}
            onChange={(event) => update({ date_from: event.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="journal-to">по</label>
          <input
            id="journal-to"
            type="date"
            value={filters.date_to ?? ""}
            onChange={(event) => update({ date_to: event.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="journal-suitable">Годность</label>
          <select
            id="journal-suitable"
            value={filters.suitable ?? ""}
            onChange={(event) => update({ suitable: event.target.value })}
          >
            <option value="">любая</option>
            <option value="true">пригодно</option>
            <option value="false">непригодно</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="journal-review">Сверка</label>
          <select
            id="journal-review"
            value={filters.needs_review ?? ""}
            onChange={(event) => update({ needs_review: event.target.value })}
          >
            <option value="">все</option>
            <option value="true">ждут сверки</option>
            <option value="false">сверены</option>
          </select>
        </div>
        <button onClick={() => void downloadJournal(filters)}>Выгрузить в Excel</button>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Протокол</th>
              <th>Поверка</th>
              <th>СИ</th>
              <th>Зав. №</th>
              <th>Владелец</th>
              <th>Адрес</th>
              <th>Поверитель</th>
              <th>Годность</th>
              <th>Состояние</th>
              <th>След. поверка</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="num">{row.protocol_number || "—"}</td>
                <td className="num">{formatDate(row.verified_at)}</td>
                <td>
                  {row.si_name}
                  <div className="sub">{row.si_registry_number}</div>
                </td>
                <td className="num">{row.serial_number}</td>
                <td>{row.owner || "—"}</td>
                <td>{row.address || "—"}</td>
                <td>{row.verifier}</td>
                <td>
                  {row.suitable ? (
                    <span className="pill ok">пригодно</span>
                  ) : (
                    <span className="pill bad">непригодно</span>
                  )}
                </td>
                <td>
                  <StatusPill row={row} />
                </td>
                <td className="num">{formatDate(row.next_verification_date)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {loading && <div className="loading">Загружаю…</div>}
        {!loading && rows.length === 0 && (
          <div className="empty">Под эти фильтры ничего не попало</div>
        )}
      </div>

      <div className="pager">
        <button disabled={page <= 1} onClick={() => update({ page: page - 1 })}>
          Назад
        </button>
        <span>
          {page} из {pages} · всего {total}
        </span>
        <button disabled={page >= pages} onClick={() => update({ page: page + 1 })}>
          Вперёд
        </button>
      </div>
    </>
  );
}
