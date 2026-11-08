import { useCallback, useEffect, useState } from "react";
import {
  approveVerificationTemplate,
  downloadTemplatePreview,
  fetchVerificationTemplate,
  fetchVerificationTemplates,
  type VerificationTemplate,
} from "../api";
import Icon from "../ui/Icon";
import { Alert, EmptyState, Spinner } from "../ui/controls";

/**
 * Шаблоны поверки СИ (всё, кроме счётчиков воды) — claude/templates.md.
 *
 * Шаблон составляет ассистент по методике поверки (карточка «Подтвердить» в чате), метролог
 * здесь сверяет его с методикой — параметры прибора, операции, точки, формулы, допуски,
 * пустой протокол — и подтверждает. Протокол по непроверенному шаблону не подписывается.
 */
export default function VerificationTemplates({ canReview }: { canReview: boolean }) {
  const [items, setItems] = useState<VerificationTemplate[]>([]);
  const [open, setOpen] = useState<VerificationTemplate | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await fetchVerificationTemplates(true));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить шаблоны");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (item: VerificationTemplate) => {
    if (open?.id === item.id) {
      setOpen(null);
      return;
    }
    try {
      setOpen(await fetchVerificationTemplate(item.id));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось открыть шаблон");
    }
  };

  const approve = async (item: VerificationTemplate) => {
    setBusy(true);
    setError("");
    try {
      setOpen(await approveVerificationTemplate(item.id));
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось подтвердить");
    } finally {
      setBusy(false);
    }
  };

  const preview = (item: VerificationTemplate, fmt: "pdf" | "docx") =>
    downloadTemplatePreview(item.id, fmt).catch((exc) =>
      setError(exc instanceof Error ? exc.message : "Не удалось собрать протокол"),
    );

  return (
    <>
      <Alert tone="info">
        Шаблон — как поверять прибор и как выглядит протокол. Составляет ассистент («сделай шаблон протокола
        поверки для манометров ДМ»), проверяет метролог. Поверитель по шаблону вносит только показания.
      </Alert>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {loading && <Spinner />}
      {!loading && items.length === 0 && (
        <EmptyState icon="layers" title="Шаблонов поверки пока нет">
          Попросите ассистента составить первый — например, для манометров или весов.
        </EmptyState>
      )}
      <div className="card-list">
        {items.map((item) => (
          <div className="card" key={item.id}>
            <div className="card-head">
              <div className="grow">
                <b>{item.title}</b>
                <div className="sub">
                  {item.si_type_name || `все типы: ${item.family_name}`} · {item.plan.method}
                </div>
                <div className="pills" style={{ marginTop: 6 }}>
                  {item.needs_review ? <span className="pill warn">ждёт проверки</span> : <span className="pill ok">проверен</span>}
                  {!item.is_active && <span className="pill muted">выключен</span>}
                  {item.created_via_assistant && <span className="tag info"><Icon name="sparkle" size={11} /> составил ассистент</span>}
                </div>
              </div>
              <div className="actions">
                {canReview && item.needs_review && (
                  <button type="button" className="primary sm" disabled={busy} onClick={() => void approve(item)}>
                    <Icon name="check" size={14} /> Всё верно
                  </button>
                )}
                <button type="button" className="sm" onClick={() => void toggle(item)}>
                  {open?.id === item.id ? "Свернуть" : "Открыть"}
                </button>
              </div>
            </div>
            {open?.id === item.id && (
              <div className="kv" style={{ marginTop: 14, marginBottom: 0, background: "var(--surface)" }}>
                <div>
                  <span className="k">Параметры прибора</span>
                  <span className="v">
                    {open.plan.params.map((p) => `${p.label}${p.from_type ? " (из типа)" : " (вносит поверитель)"}`).join("; ") || "—"}
                  </span>
                </div>
                <div>
                  <span className="k">Операции</span>
                  <span className="v">{open.plan.operations.map((op) => op.label).join("; ")}</span>
                </div>
                <div>
                  <span className="k">Поверяемые точки</span>
                  <span className="v">{open.plan.modes.map((m) => m.label || m.setpoint).join("; ")}</span>
                </div>
                <div>
                  <span className="k">В каждой точке</span>
                  <span className="v">
                    {open.plan.points.map((p) => p.label).join("; ")} × {open.plan.repeats}
                  </span>
                </div>
                <div>
                  <span className="k">Характеристики</span>
                  <span className="v">{open.plan.characteristics.map((c) => `${c.label} (${c.text})`).join("; ")}</span>
                </div>
                {open.sources && open.sources.length > 0 && (
                  <div>
                    <span className="k">Источники</span>
                    <span className="v">{open.sources.join("; ")}</span>
                  </div>
                )}
                {open.note && (
                  <div>
                    <span className="k">Примечание</span>
                    <span className="v">{open.note}</span>
                  </div>
                )}
                <div className="actions">
                  <button type="button" className="sm" onClick={() => void preview(item, "pdf")}><Icon name="download" size={14} /> Пустой протокол PDF</button>
                  <button type="button" className="sm" onClick={() => void preview(item, "docx")}><Icon name="download" size={14} /> Word</button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
