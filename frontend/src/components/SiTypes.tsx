import { useCallback, useEffect, useState } from "react";
import { confirmSiType, fetchSiTypes, type Me, type SiType } from "../api";
import { rememberSiType } from "../offline/sync";
import SiTypeForm from "./SiTypeForm";

const REVIEWER_ROLES = new Set(["owner", "head", "metrologist"]);

/**
 * Справочник типов СИ — вместо раздела в админке.
 *
 * Метролог открывает вкладку «Ждут проверки», сверяет характеристики с
 * описанием типа в Госреестре, при необходимости правит и подтверждает.
 */
export default function SiTypes({ me }: { me: Me | null }) {
  const canReview = REVIEWER_ROLES.has(me?.role ?? "");
  const [onlyPending, setOnlyPending] = useState(canReview);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SiType[]>([]);
  const [pendingTotal, setPendingTotal] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, pending] = await Promise.all([
        fetchSiTypes({ needs_review: onlyPending, q: query.trim() }),
        fetchSiTypes({ needs_review: true }),
      ]);
      setItems(list);
      setPendingTotal(pending.length);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить справочник");
    } finally {
      setLoading(false);
    }
  }, [onlyPending, query]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 250);
    return () => clearTimeout(timer);
  }, [load]);

  const confirm = async (item: SiType) => {
    setBusy(item.id);
    setError("");
    try {
      const saved = await confirmSiType(item.id);
      void rememberSiType(saved);
      setOpenId(null);
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось подтвердить");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="narrow">
      <div className="toolbar">
        <div className="segmented">
          <button type="button" className={onlyPending ? "active" : ""} onClick={() => setOnlyPending(true)}>
            Ждут проверки{pendingTotal > 0 ? ` · ${pendingTotal}` : ""}
          </button>
          <button type="button" className={!onlyPending ? "active" : ""} onClick={() => setOnlyPending(false)}>
            Все типы
          </button>
        </div>
        <input
          className="grow"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по названию, номеру, изготовителю"
        />
        <button type="button" className="primary" onClick={() => setAdding(true)}>
          + Новый тип
        </button>
      </div>

      {adding && (
        <SiTypeForm
          title="Новый тип СИ"
          onSaved={(saved) => {
            void rememberSiType(saved);
            setAdding(false);
            void load();
          }}
          onCancel={() => setAdding(false)}
        />
      )}

      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Загружаю…</div>}
      {!loading && items.length === 0 && (
        <div className="empty">{onlyPending ? "Новых типов на проверку нет" : "Ничего не нашлось"}</div>
      )}

      <div className="card-list">
        {items.map((item) => (
          <div className="card" key={item.id}>
            <div className="card-head">
              <div className="grow">
                <b>{item.name}</b>
                <div className="sub">
                  № {item.registry_number} · {item.manufacturer || "изготовитель не указан"} · МПИ{" "}
                  {item.verification_interval_months} мес.
                </div>
                {item.needs_review ? (
                  <div className="sub">
                    <span className="pill warn">ждёт проверки</span> завёл {item.created_by_name || "—"}
                  </div>
                ) : (
                  item.reviewed_by_name && <div className="sub">проверил {item.reviewed_by_name}</div>
                )}
              </div>
              <div className="actions">
                {canReview && item.needs_review && (
                  <button type="button" className="primary" disabled={busy === item.id} onClick={() => void confirm(item)}>
                    Всё верно
                  </button>
                )}
                {(canReview || item.needs_review) && (
                  <button type="button" onClick={() => setOpenId(openId === item.id ? null : item.id)}>
                    {openId === item.id ? "Свернуть" : "Открыть"}
                  </button>
                )}
              </div>
            </div>
            {openId === item.id && (
              <SiTypeForm
                key={item.id}
                existing={item}
                onSaved={(saved) => {
                  void rememberSiType(saved);
                  setOpenId(null);
                  void load();
                }}
                onCancel={() => setOpenId(null)}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
