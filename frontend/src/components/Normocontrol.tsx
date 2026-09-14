import { useCallback, useEffect, useState } from "react";
import { applyNumbering, fetchScopes, previewNumbering, type Preview, type Scope } from "../api";

/**
 * Экран нормоконтроля.
 *
 * Главное правило: метролог сначала видит, что даст пересчёт, и только потом
 * применяет. Поэтому кнопка «Пересчитать» появляется лишь после предпросмотра.
 */
export default function Normocontrol() {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [previews, setPreviews] = useState<Record<number, Preview>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setScopes(await fetchScopes());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить области нумерации");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const preview = async (scope: Scope) => {
    setBusy(scope.id);
    setError("");
    try {
      const result = await previewNumbering(scope.id);
      setPreviews((current) => ({ ...current, [scope.id]: result }));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось получить предпросмотр");
    } finally {
      setBusy(null);
    }
  };

  const apply = async (scope: Scope) => {
    setBusy(scope.id);
    setError("");
    try {
      await applyNumbering(scope.id);
      setPreviews((current) => {
        const next = { ...current };
        delete next[scope.id];
        return next;
      });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось пересчитать номера");
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <div className="loading">Загружаю…</div>;

  return (
    <>
      {error && <div className="error">{error}</div>}

      {scopes.length === 0 && (
        <div className="empty">Областей нумерации пока нет — заведите поверки</div>
      )}

      <div className="scopes">
        {scopes.map((scope) => {
          const shown = previews[scope.id];
          return (
            <div className="scope" key={scope.id}>
              <h3>{scope.title}</h3>
              <div className="who">
                {scope.employee}
                {scope.year ? ` · ${scope.year} год` : ""}
              </div>

              <div className="counts">
                <span className="pill muted">без номера: {scope.drafts}</span>
                <span className="pill muted">пронумеровано: {scope.numbered}</span>
                <span className="pill ok">подписано: {scope.signed}</span>
                {scope.published > 0 && <span className="pill ok">в ФИФ: {scope.published}</span>}
                <span className="pill muted">
                  запечатано до: {scope.sealed_high_water || "—"}
                </span>
              </div>

              <div className="actions">
                <button disabled={busy === scope.id} onClick={() => void preview(scope)}>
                  Показать, что изменится
                </button>
                {shown?.changed && (
                  <button
                    className="primary"
                    disabled={busy === scope.id}
                    onClick={() => void apply(scope)}
                  >
                    Пересчитать номера
                  </button>
                )}
              </div>

              {shown && !shown.changed && (
                <div className="preview">
                  <h4>Менять нечего</h4>
                </div>
              )}

              {shown?.changed && (
                <div className="preview">
                  <h4>
                    Присвоится {shown.assigned.length}, сдвинется {shown.renumbered.length}
                  </h4>
                  <ul>
                    {shown.lines.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}

              {scope.chronology_breaks.length > 0 && (
                <div className="breaks">
                  Номера идут вразрез с датами поверки:
                  <ul>
                    {scope.chronology_breaks.map((item, index) => (
                      <li key={index}>
                        {item.previous} → {item.current}
                        {item.reason ? ` — ${item.reason}` : " — причина не указана, разобраться"}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
