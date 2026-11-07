import { useCallback, useEffect, useState } from "react";
import { addTenderSearch, fetchTenders, updateTender, type Tender, type TenderSearchInfo } from "../api";

const STATUSES: [string, string][] = [
  ["new", "Новые"], ["watching", "Изучаем"], ["applied", "Подали"], ["won", "Выиграли"],
  ["lost", "Не выиграли"], ["skipped", "Не интересно"],
];
const money = (v: string | null) =>
  v ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Number(v))} ₽` : "цена не указана";
const date = (v: string | null) => (v ? new Date(v).toLocaleDateString("ru-RU") : "—");

/** Закупки на поверку из ЕИС: сохранённые поиски проверяются три раза в день. */
export default function Tenders() {
  const [status, setStatus] = useState("new");
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [searches, setSearches] = useState<TenderSearchInfo[]>([]);
  const [query, setQuery] = useState("поверка счетчиков воды");
  const [region, setRegion] = useState("43");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchTenders(status);
      setTenders(data.tenders);
      setSearches(data.searches);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить закупки");
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await addTenderSearch(query, region);
      setMessage(result.detail ?? `Найдено новых: ${result.found ?? 0}`);
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Поиск не выполнен");
    } finally {
      setBusy(false);
    }
  };

  const move = async (tender: Tender, next: string) => {
    try {
      await updateTender(tender.id, { status: next });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не сохранилось");
    }
  };

  return (
    <div className="tenders">
      {error && <div className="error">{error}</div>}
      {message && <div className="notice"><span>{message}</span></div>}
      <div className="scope">
        <h3>Поиски</h3>
        {searches.map((s) => (
          <div className="hint" key={s.id}>
            «{s.query}»{s.region ? ` · регион ${s.region}` : ""} · {s.last_run_at ? `проверено ${date(s.last_run_at)}` : "ещё не выполнялся"}
            {s.last_error ? ` · ${s.last_error}` : ""}
          </div>
        ))}
        <div className="actions">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Что искать" />
          <input value={region} onChange={(e) => setRegion(e.target.value)} placeholder="Код региона" size={4} />
          <button className="primary" disabled={busy || query.trim().length < 3} onClick={() => void add()}>
            Добавить поиск
          </button>
        </div>
      </div>

      <div className="toolbar">
        <div className="segmented" role="tablist">
          {STATUSES.map(([key, label]) => (
            <button type="button" key={key} className={status === key ? "active" : ""} onClick={() => setStatus(key)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="scopes">
        {tenders.map((t) => (
          <div className="scope" key={t.id}>
            <h3><a href={t.url} target="_blank" rel="noopener">{t.title}</a></h3>
            <div className="who">{t.customer || "заказчик не указан"}</div>
            <div className="counts">
              <span className="pill">{money(t.price)}</span>
              {t.deadline && <span className="pill muted">заявки до {date(t.deadline)}</span>}
              {t.law && <span className="pill muted">{t.law}-ФЗ</span>}
              <span className="pill muted">№ {t.reg_number}</span>
            </div>
            <div className="actions">
              {STATUSES.filter(([key]) => key !== t.status).slice(0, 4).map(([key, label]) => (
                <button key={key} className="link" onClick={() => void move(t, key)}>{label}</button>
              ))}
            </div>
          </div>
        ))}
        {tenders.length === 0 && <div className="empty">Закупок в этом состоянии нет</div>}
      </div>
    </div>
  );
}
