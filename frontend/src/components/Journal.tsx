import { useCallback, useEffect, useState } from "react";
import Icon from "../ui/Icon";
import { Alert, EmptyState, PageActions, SearchField, Spinner } from "../ui/controls";
import { PHONE, useMediaQuery } from "../ui/hooks";
import {
  downloadJournal,
  downloadVerificationProtocol,
  fetchJournal,
  type JournalFilters,
  type JournalRow,
} from "../api";

const PAGE_SIZE = 50;

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "—";

const isOverdue = (value: string | null) => Boolean(value && value < new Date().toISOString().slice(0, 10));

function StatusPill({ row }: { row: JournalRow }) {
  if (row.needs_review) return <span className="pill warn">на сверку</span>;
  if (row.protocol_status === "signed") return <span className="pill ok">подписан</span>;
  if (row.protocol_status === "published") return <span className="pill ok">в ФИФ</span>;
  if (row.protocol_status === "void") return <span className="pill bad">аннулирован</span>;
  if (row.protocol_status === "numbered") return <span className="pill muted">номер присвоен</span>;
  return <span className="pill muted">черновик</span>;
}

export default function Journal() {
  const phone = useMediaQuery(PHONE);
  const [filters, setFilters] = useState<JournalFilters>({ page: 1 });
  const [rows, setRows] = useState<JournalRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
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
  const filtered = Boolean(filters.q || filters.date_from || filters.date_to || filters.suitable || filters.needs_review);

  const exportXlsx = async () => {
    setExporting(true);
    try {
      await downloadJournal(filters);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось выгрузить журнал");
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <PageActions>
        <button type="button" onClick={() => void exportXlsx()} disabled={exporting} aria-label="Выгрузить в Excel" title="Выгрузить в Excel">
          <Icon name="download" size={16} />
          <span className="hide-phone">{exporting ? "Готовлю файл…" : "Выгрузить в Excel"}</span>
        </button>
      </PageActions>

      <div className="filters">
        <SearchField
          value={filters.q ?? ""}
          onChange={(q) => update({ q })}
          placeholder="Заводской номер, тип СИ, адрес, № протокола"
          className="grow"
        />
        {phone && (
          <button type="button" className={showFilters || filtered ? "tinted" : ""} onClick={() => setShowFilters((v) => !v)}>
            Фильтры{filtered ? " · есть" : ""}
          </button>
        )}
        {(!phone || showFilters) && <>
        <div className="field date">
          <label htmlFor="journal-from">Поверено с</label>
          <input id="journal-from" type="date" value={filters.date_from ?? ""} onChange={(event) => update({ date_from: event.target.value })} />
        </div>
        <div className="field date">
          <label htmlFor="journal-to">по</label>
          <input id="journal-to" type="date" value={filters.date_to ?? ""} onChange={(event) => update({ date_to: event.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="journal-suitable">Годность</label>
          <select id="journal-suitable" value={filters.suitable ?? ""} onChange={(event) => update({ suitable: event.target.value })}>
            <option value="">любая</option>
            <option value="true">пригодно</option>
            <option value="false">непригодно</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="journal-review">Сверка</label>
          <select id="journal-review" value={filters.needs_review ?? ""} onChange={(event) => update({ needs_review: event.target.value })}>
            <option value="">все</option>
            <option value="true">ждут сверки</option>
            <option value="false">сверены</option>
          </select>
        </div>
        </>}
        {filtered && (
          <button type="button" className="ghost" onClick={() => setFilters({ page: 1 })}>
            Сбросить
          </button>
        )}
      </div>

      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      {phone && (
        <>
          <div className="m-list">
            {rows.map((row) => (
              <div className="m-row" key={row.id}>
                <div className="grow">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="cell-mono" style={{ fontWeight: 600 }}>{row.protocol_number || "без номера"}</span>
                    <span className="sub num">{formatDate(row.verified_at)}</span>
                  </div>
                  <div className="m-title">{row.si_name} · № {row.serial_number}</div>
                  <div className="m-meta">{[row.owner, row.address].filter(Boolean).join(" · ")}</div>
                  <div className="row wrap" style={{ marginTop: 4 }}>
                    {row.suitable ? <span className="pill ok">пригодно</span> : <span className="pill bad">непригодно</span>}
                    <StatusPill row={row} />
                    <span className="grow" />
                    {row.protocol_formats.map((fmt) => (
                      <button
                        type="button"
                        className={`dl-btn ${fmt === "docx" ? "word" : ""}`}
                        key={fmt}
                        onClick={() =>
                          void downloadVerificationProtocol(row.id, fmt).catch((exc) =>
                            setError(exc instanceof Error ? exc.message : "Не удалось скачать протокол"),
                          )
                        }
                      >
                        {fmt === "pdf" ? "PDF" : "DOCX"}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {loading && rows.length === 0 && <Spinner />}
          {!loading && rows.length === 0 && (
            <EmptyState icon="doc" title={filtered ? "Под эти фильтры ничего не попало" : "Протоколов пока нет"} />
          )}
        </>
      )}

      {!phone && <div className="table-wrap">
        <table className="data-table journal-table" style={{ minWidth: 980 }}>
          <thead>
            <tr>
              <th>Протокол</th>
              <th>Средство измерений</th>
              <th>Владелец и адрес</th>
              <th>Поверитель</th>
              <th>Результат</th>
              <th>Следующая</th>
              <th className="right">Файлы</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <div className="cell-main cell-mono">{row.protocol_number || "без номера"}</div>
                  <div className="sub num">{formatDate(row.verified_at)}</div>
                </td>
                <td>
                  <div className="cell-main">{row.si_name}</div>
                  <div className="sub">
                    <span className="cell-mono">{row.si_registry_number}</span> · зав. № <span className="cell-mono">{row.serial_number}</span>
                  </div>
                </td>
                <td style={{ maxWidth: 280 }}>
                  <div>{row.owner || "—"}</div>
                  {row.address && <div className="sub">{row.address}</div>}
                </td>
                <td>{row.verifier}</td>
                <td>
                  <div className="pills" style={{ flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
                    {row.suitable ? <span className="pill ok">пригодно</span> : <span className="pill bad">непригодно</span>}
                    <StatusPill row={row} />
                  </div>
                </td>
                <td className={`num ${row.suitable && isOverdue(row.next_verification_date) ? "due over" : ""}`}>
                  {row.suitable ? formatDate(row.next_verification_date) : "—"}
                </td>
                <td className="actions-cell">
                  <span className="dl-group">
                    {row.protocol_formats.map((fmt) => (
                      <button
                        type="button"
                        className={`dl-btn ${fmt === "docx" ? "word" : ""}`}
                        key={fmt}
                        title={fmt === "pdf" ? "Скачать протокол PDF" : "Скачать протокол Word"}
                        onClick={() =>
                          void downloadVerificationProtocol(row.id, fmt).catch((exc) =>
                            setError(exc instanceof Error ? exc.message : "Не удалось скачать протокол"),
                          )
                        }
                      >
                        {fmt === "pdf" ? "PDF" : "DOCX"}
                      </button>
                    ))}
                    {row.protocol_formats.length === 0 && <span className="muted">—</span>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {loading && rows.length === 0 && <Spinner />}
        {!loading && rows.length === 0 && (
          <EmptyState icon="doc" title={filtered ? "Под эти фильтры ничего не попало" : "Протоколов пока нет"}>
            {filtered ? "Измените или сбросьте фильтры." : "Протоколы появятся, когда поверители внесут измерения."}
          </EmptyState>
        )}
      </div>}

      {total > 0 && (
        <div className="pager">
          <span className="pager-info">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} из {total}
            {loading && <span className="spinner sm" style={{ marginLeft: 8, verticalAlign: -2 }} />}
          </span>
          {pages > 1 && (
            <div className="pager-buttons">
              <button type="button" className="icon-btn" disabled={page <= 1} onClick={() => update({ page: page - 1 })} aria-label="Назад">
                <Icon name="chevronLeft" />
              </button>
              <span>{page} / {pages}</span>
              <button type="button" className="icon-btn" disabled={page >= pages} onClick={() => update({ page: page + 1 })} aria-label="Вперёд">
                <Icon name="chevronRight" />
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
