import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import Icon from "../ui/Icon";
import { Alert, EmptyState, PageActions, SearchField, Spinner } from "../ui/controls";
import { PHONE, useMediaQuery } from "../ui/hooks";
import {
  downloadJournal,
  downloadVerificationProtocol,
  fetchJournal,
  fetchJournalVerifiers,
  type JournalFilters,
  type JournalRow,
  type JournalVerifier,
} from "../api";

const PAGE_SIZE = 50;

/** Цвета поверителей в общем журнале. Красного нет — он занят непригодными.
 * Цвет даётся по порядку карточки (по id), поэтому у человека он постоянный. */
const VERIFIER_COLORS = [
  "#0a84ff", "#ff9f0a", "#30b158", "#af52de", "#30b0c7",
  "#5856d6", "#a2845e", "#d4a800", "#00a89f", "#ff6fb1",
];

type Journals = "all" | number;

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

/** В таблице статус — просто текст в ячейке, без «таблеток». */
function StatusText({ row }: { row: JournalRow }) {
  if (row.needs_review) return <span className="st warn">на сверку</span>;
  if (row.protocol_status === "signed") return <span className="st">подписан</span>;
  if (row.protocol_status === "published") return <span className="st">в ФИФ</span>;
  if (row.protocol_status === "void") return <span className="st bad">аннулирован</span>;
  if (row.protocol_status === "numbered") return <span className="st muted">номер присвоен</span>;
  return <span className="st muted">черновик</span>;
}

function VerifierDot({ color }: { color?: string }) {
  return <span className="v-dot" style={{ background: color ?? "var(--muted)" }} aria-hidden="true" />;
}

/** Журнал: общий (все поверители, у каждого свой цвет) или журнал одного поверителя.
 * Поверитель по умолчанию открывает свой журнал, менеджер — общий. */
export default function Journal({ isManager = false }: { isManager?: boolean }) {
  const phone = useMediaQuery(PHONE);
  const [filters, setFilters] = useState<JournalFilters>({ page: 1 });
  const [verifiers, setVerifiers] = useState<JournalVerifier[] | null>(null);
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

  useEffect(() => {
    fetchJournalVerifiers()
      .then((list) => {
        setVerifiers(list);
        const mine = list.find((v) => v.is_me);
        if (!isManager && mine) setFilters((current) => ({ ...current, verifier: mine.id, page: 1 }));
      })
      // Без списка поверителей журнал всё равно работает — просто как общий.
      .catch(() => setVerifiers([]));
  }, [isManager]);

  const colors = useMemo(() => {
    const map = new Map<number, string>();
    (verifiers ?? []).forEach((v, index) => map.set(v.id, VERIFIER_COLORS[index % VERIFIER_COLORS.length]));
    return map;
  }, [verifiers]);

  // Поиск не дёргает сервер на каждую букву. Пока не знаем, чей журнал открыть, — ждём.
  useEffect(() => {
    if (verifiers === null) return;
    const timer = setTimeout(() => void load(filters), filters.q ? 300 : 0);
    return () => clearTimeout(timer);
  }, [filters, load, verifiers]);

  const journal: Journals = filters.verifier ?? "all";
  const current = journal === "all" ? null : verifiers?.find((v) => v.id === journal) ?? null;
  const shared = journal === "all";
  const openJournal = (next: Journals) =>
    setFilters((f) => ({ ...f, verifier: next === "all" ? undefined : next, page: 1 }));
  const totalAll = (verifiers ?? []).reduce((sum, v) => sum + v.count, 0);
  const rowStyle = (row: JournalRow) =>
    (shared ? { "--vc": colors.get(row.verifier_id) ?? "transparent" } : undefined) as CSSProperties | undefined;

  const update = (patch: Partial<JournalFilters>) =>
    setFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));

  const page = filters.page ?? 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = Boolean(filters.q || filters.date_from || filters.date_to || filters.suitable || filters.needs_review);

  const exportXlsx = async () => {
    setExporting(true);
    try {
      await downloadJournal(filters, current?.full_name ?? "");
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

      {verifiers && verifiers.length > 0 && (
        <div className="chip-row journals" role="tablist" aria-label="Чей журнал">
          <button
            type="button"
            role="tab"
            aria-selected={shared}
            className={`chip ${shared ? "active" : ""}`}
            onClick={() => openJournal("all")}
          >
            Общий журнал <span className="chip-count">{totalAll}</span>
          </button>
          {verifiers.map((v) => (
            <button
              type="button"
              role="tab"
              key={v.id}
              aria-selected={journal === v.id}
              className={`chip ${journal === v.id ? "active" : ""}`}
              style={{ "--vc": colors.get(v.id) } as CSSProperties}
              onClick={() => openJournal(v.id)}
              title={v.is_me ? "Мой журнал" : `Журнал поверителя ${v.full_name}`}
            >
              <VerifierDot color={colors.get(v.id)} />
              {v.full_name}{v.is_me ? " (я)" : ""} <span className="chip-count">{v.count}</span>
            </button>
          ))}
        </div>
      )}

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
          <button type="button" className="ghost" onClick={() => setFilters({ page: 1, verifier: filters.verifier })}>
            Сбросить
          </button>
        )}
      </div>

      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      {phone && (
        <>
          <div className="m-list">
            {rows.map((row) => (
              <div className={`m-row ${row.suitable ? "" : "unfit"} ${shared ? "by-verifier" : ""}`} key={row.id} style={rowStyle(row)}>
                <div className="grow">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="cell-mono" style={{ fontWeight: 600 }}>{row.protocol_number || "без номера"}</span>
                    <span className="sub num">{formatDate(row.verified_at)}</span>
                  </div>
                  <div className="m-title">{row.si_name} · № {row.serial_number}</div>
                  <div className="m-meta">{[row.owner, row.address].filter(Boolean).join(" · ")}</div>
                  {shared && (
                    <div className="m-meta v-name"><VerifierDot color={colors.get(row.verifier_id)} />{row.verifier}</div>
                  )}
                  <div className="row wrap" style={{ marginTop: 4 }}>
                    {!row.suitable && <span className="pill bad">непригодно</span>}
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

      {!phone && <div className="table-wrap sheet-wrap">
        <table className={`sheet journal-sheet ${shared ? "by-verifier" : ""}`}>
          <thead>
            <tr>
              <th className="rn">№</th>
              <th>Дата поверки</th>
              <th>№ протокола</th>
              <th>Тип СИ</th>
              <th>Рег. № ГРСИ</th>
              <th>Зав. №</th>
              <th>Владелец</th>
              <th>Адрес</th>
              {shared && <th>Поверитель</th>}
              <th>Действует до</th>
              <th>Результат</th>
              <th>Статус</th>
              <th>Прочие сведения</th>
              <th>Файлы</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.id} className={row.suitable ? "" : "unfit"} style={rowStyle(row)}>
                <td className="rn num">{(page - 1) * PAGE_SIZE + index + 1}</td>
                <td className="num">{formatDate(row.verified_at)}</td>
                <td className="cell-mono nowrap">{row.protocol_number || <span className="muted">без номера</span>}</td>
                <td className="wide">{row.si_name}</td>
                <td className="cell-mono nowrap">{row.si_registry_number}</td>
                <td className="cell-mono nowrap">{row.serial_number}</td>
                <td className="wide">{row.owner}</td>
                <td className="wide">{row.address}</td>
                {shared && (
                  <td className="nowrap v-name"><VerifierDot color={colors.get(row.verifier_id)} />{row.verifier}</td>
                )}
                <td className={`num ${row.suitable && isOverdue(row.next_verification_date) ? "due over" : ""}`}>
                  {row.suitable ? formatDate(row.next_verification_date) : ""}
                </td>
                <td className="nowrap unfit-cell">{row.suitable ? "" : "непригодно"}</td>
                <td className="nowrap"><StatusText row={row} /></td>
                <td className="note">{row.journal_note}</td>
                <td className="nowrap files">
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
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {loading && rows.length === 0 && <Spinner />}
        {!loading && rows.length === 0 && (
          <EmptyState icon="doc" title={filtered ? "Под эти фильтры ничего не попало" : "Протоколов пока нет"}>
            {filtered
              ? "Измените или сбросьте фильтры."
              : current
                ? `В журнале ${current.full_name} пока нет записей.`
                : "Протоколы появятся, когда поверители внесут измерения."}
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
