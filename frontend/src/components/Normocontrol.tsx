import { useCallback, useEffect, useState } from "react";
import { applyNumbering, fetchScopes, fetchSiTypes, previewNumbering, type Preview, type Scope } from "../api";
import Icon from "../ui/Icon";
import { Alert, EmptyState, Spinner } from "../ui/controls";

/**
 * Экран нормоконтроля.
 *
 * Главное правило: метролог сначала видит, что даст пересчёт, и только потом
 * применяет. Поэтому кнопка «Пересчитать» появляется лишь после предпросмотра.
 */
export default function Normocontrol({ onOpenTypes }: { onOpenTypes?: () => void }) {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [previews, setPreviews] = useState<Record<number, Preview>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [pendingTypes, setPendingTypes] = useState(0);

  // Типы, заведённые поверителями: пока метролог их не проверит, протоколы
  // по ним не подписываются (apps.verification.numbering.seal).
  useEffect(() => {
    fetchSiTypes({ needs_review: true })
      .then((list) => setPendingTypes(list.length))
      .catch(() => setPendingTypes(0));
  }, []);

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

  if (loading) return <Spinner />;

  return (
    <>
      {pendingTypes > 0 && (
        <Alert tone="warn">
          Новых типов СИ ждут проверки: <b>{pendingTypes}</b>. Пока тип не проверен, протоколы по нему не подписываются.
          {onOpenTypes && (
            <div style={{ marginTop: 8 }}>
              <button type="button" className="sm" onClick={onOpenTypes}>
                Проверить типы <Icon name="arrowRight" size={14} />
              </button>
            </div>
          )}
        </Alert>
      )}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}

      {scopes.length === 0 && (
        <EmptyState icon="layers" title="Областей нумерации пока нет">
          Они появятся, когда поверители заведут первые поверки.
        </EmptyState>
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
                {` · запечатано до № ${scope.sealed_high_water || "—"}`}
              </div>

              <div className="stat-row">
                <div><b>{scope.drafts}</b><span>без номера</span></div>
                <div><b>{scope.numbered}</b><span>пронумеровано</span></div>
                <div className="ok"><b>{scope.signed}</b><span>подписано</span></div>
                <div><b>{scope.published}</b><span>в ФИФ</span></div>
              </div>

              {shown && !shown.changed && (
                <Alert tone="ok">Нумерация в порядке — менять нечего.</Alert>
              )}

              {shown?.changed && (
                <div className="preview" style={{ marginTop: 0, marginBottom: 12 }}>
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
                <div className="breaks" style={{ marginTop: 0, marginBottom: 12 }}>
                  <b>Номера идут вразрез с датами поверки:</b>
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

              <div className="actions">
                <button type="button" disabled={busy === scope.id} onClick={() => void preview(scope)}>
                  <Icon name="eye" size={16} />
                  {shown ? "Проверить ещё раз" : "Показать, что изменится"}
                </button>
                {shown?.changed && (
                  <button type="button" className="primary" disabled={busy === scope.id} onClick={() => void apply(scope)}>
                    Пересчитать номера
                  </button>
                )}
                {busy === scope.id && <span className="spinner sm" />}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
