/** Проверка шаблона поверки метрологом: состав по методике, пустой протокол, «Проверен». */

import { useEffect, useState } from "react";
import {
  approveVerificationTemplate,
  deleteVerificationTemplate,
  downloadTemplatePreview,
  fetchVerificationTemplate,
  type VerificationTemplate,
} from "../../api";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { confirmDialog } from "../../ui/Modal";
import { Sheet } from "../../ui/Sheet";
import { SkeletonText } from "../../ui/Skeleton";
import { toast } from "../../ui/Toast";
import { Alert } from "../../ui/controls";
import { STATUS_LABEL, STATUS_PILL, templateStatus } from "./api";

export default function ReviewSheet({
  templateId, isReviewer, onClose, onChanged, onOpenStudio,
}: {
  templateId: number | null;
  isReviewer: boolean;
  onClose: () => void;
  onChanged: () => void;
  /** Открыть чат-студию по типу шаблона (свой чат метролога). */
  onOpenStudio?: (siTypeId: number) => void;
}) {
  const [item, setItem] = useState<VerificationTemplate | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!templateId) return;
    let cancelled = false;
    setItem(null);
    setError("");
    fetchVerificationTemplate(templateId)
      .then((t) => !cancelled && setItem(t))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  const approve = async () => {
    if (!item || busy || !item.needs_review) return;
    setBusy(true);
    setError("");
    try {
      setItem(await approveVerificationTemplate(item.id));
      toast.success("Шаблон проверен", { description: "Протоколы по нему можно подписывать" });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!item || busy) return;
    const ok = await confirmDialog({
      title: `Удалить шаблон «${item.title}»?`,
      text: "Проведённые поверки хранят свой снимок шаблона и не изменятся. Если по шаблону уже вносили " +
        "измерения, он не сотрётся, а выключится.",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const reply = await deleteVerificationTemplate(item.id);
      toast.success(reply?.archived ? "Шаблон выключен" : "Шаблон удалён", reply?.detail ? { description: reply.detail } : undefined);
      onChanged();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useHotkey("mod+enter", () => void approve(), { allowInInputs: true, enabled: !!templateId && isReviewer, global: true });

  const preview = (fmt: "pdf" | "docx") =>
    item && downloadTemplatePreview(item.id, fmt).catch((e) => setError((e as Error).message));

  const status = item ? templateStatus(item) : "review";
  const plan = item?.plan;
  return (
    <Sheet
      open={!!templateId}
      onClose={onClose}
      size="lg"
      title={item?.title ?? "Шаблон поверки"}
      subtitle={item ? `${item.si_type_name || `все типы: ${item.family_name}`} · ${item.plan.method || "методика не указана"}` : undefined}
      footer={
        item && (
          <div className="si-step-actions">
            <button type="button" onClick={() => void preview("pdf")}><Icon name="download" size={14} /> Пустой протокол PDF</button>
            <button type="button" onClick={() => void preview("docx")}><Icon name="download" size={14} /> Word</button>
            {onOpenStudio && item.si_type && (
              <button type="button" onClick={() => onOpenStudio(item.si_type!)}><Icon name="sparkle" size={14} /> Править с ассистентом</button>
            )}
            {isReviewer && (
              <button type="button" className="ghost danger" disabled={busy} onClick={() => void remove()}>
                <Icon name="trash" size={14} /> Удалить
              </button>
            )}
            {isReviewer && item.needs_review && (
              <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>
                <Icon name="check" size={16} /> Проверен — всё верно <Kbd keys="mod+enter" className="on-accent kbd-hint" />
              </button>
            )}
          </div>
        )
      }
    >
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {!item && !error && <SkeletonText lines={8} />}
      {item && plan && (
        <div className="si-review">
          <div className="pills">
            <span className={`pill ${STATUS_PILL[status]}`}>{STATUS_LABEL[status]}</span>
            {!item.is_active && <span className="pill muted">выключен</span>}
            {item.created_via_assistant && <span className="tag info"><Icon name="sparkle" size={11} /> составил ассистент</span>}
            {item.created_by && <span className="sub">завёл {item.created_by}</span>}
            {item.reviewed_by && <span className="sub">проверил {item.reviewed_by}</span>}
          </div>
          {isReviewer && item.needs_review && (
            <Alert tone="info">Сверьте с методикой: параметры, операции, точки, формулы и допуски, эталоны; откройте пустой протокол.</Alert>
          )}
          <dl className="tpl-dl">
            <Row label="Параметры прибора" items={plan.params.map((p) => `${p.label}${p.from_type ? " (из типа)" : " (вносит поверитель)"}`)} />
            <Row label="Операции" items={plan.operations.map((op) => op.label)} />
            <Row label="Поверяемые точки" items={plan.modes.map((m) => m.label || m.setpoint)} />
            <Row label="В каждой точке" items={plan.points.map((p) => `${p.label}${plan.repeats > 1 ? ` × ${plan.repeats}` : ""}`)} />
            <Row label="Характеристики" items={plan.characteristics.map((c) => `${c.label} — ${c.text}`)} />
            <Row
              label="Эталоны"
              items={item.kits?.length ? item.kits.map((k) => `${k.scope || k.section}${k.employee_name ? ` (${k.employee_name})` : ""}`) : ["не выбраны — поверитель отметит эталоны сам"]}
            />
            {item.sources && item.sources.length > 0 && <Row label="Источники" items={item.sources} />}
            {item.note && <Row label="Примечание" items={[item.note]} />}
          </dl>
        </div>
      )}
    </Sheet>
  );
}

function Row({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{items.length ? <ul>{items.map((v, i) => <li key={i}>{v}</li>)}</ul> : "—"}</dd>
    </div>
  );
}
