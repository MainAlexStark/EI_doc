import { useCallback, useEffect, useRef, useState } from "react";
import { confirmSiType, fetchSiTypes, type Me, type SiType } from "../api";
import { rememberSiType } from "../offline/sync";
import Icon from "../ui/Icon";
import { Modal } from "../ui/Modal";
import { Alert, EmptyState, PageActions, SearchField, Segmented, Spinner } from "../ui/controls";
import FifTypeSearch from "./FifTypeSearch";
import SiTypeForm, { type SiTypeDraft } from "./SiTypeForm";
import StandardKits from "./StandardKits";
import VerificationTemplates from "./VerificationTemplates";

const REVIEWER_ROLES = new Set(["owner", "head", "metrologist"]);

type View = "pending" | "all" | "templates" | "kits";

/**
 * Справочник типов СИ — вместо раздела в админке.
 *
 * Метролог открывает «Ждут проверки», сверяет характеристики с описанием типа
 * в Госреестре, при необходимости правит и подтверждает. Карточка типа — в окне.
 */
export default function SiTypes({ me, onChanged }: { me: Me | null; onChanged?: () => void }) {
  const canReview = REVIEWER_ROLES.has(me?.role ?? "");
  const [view, setViewState] = useState<View>(canReview ? "pending" : "all");
  const touched = useRef(false);
  const setView = (next: View) => {
    touched.current = true;
    setViewState(next);
  };
  // Роль приходит после первой отрисовки — метролог по умолчанию попадает в «Ждут проверки».
  useEffect(() => {
    if (canReview && !touched.current) setViewState("pending");
  }, [canReview]);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SiType[]>([]);
  const [pendingTotal, setPendingTotal] = useState(0);
  const [open, setOpen] = useState<SiType | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<SiTypeDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (view === "templates" || view === "kits") return;
    setLoading(true);
    setError("");
    try {
      const [list, pending] = await Promise.all([
        fetchSiTypes({ needs_review: view === "pending", q: query.trim() }),
        fetchSiTypes({ needs_review: true }),
      ]);
      setItems(list);
      setPendingTotal(pending.length);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить справочник");
    } finally {
      setLoading(false);
    }
  }, [view, query]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [load, query]);

  const confirm = async (item: SiType) => {
    setBusy(item.id);
    setError("");
    try {
      const saved = await confirmSiType(item.id);
      void rememberSiType(saved);
      setOpen(null);
      await load();
      onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось подтвердить");
    } finally {
      setBusy(null);
    }
  };

  const closeAdding = () => {
    setAdding(false);
    setDraft(null);
  };

  const saved = (item: SiType) => {
    void rememberSiType(item);
    closeAdding();
    setOpen(null);
    void load();
    onChanged?.();
  };

  return (
    <div className="narrow" style={{ maxWidth: 920 }}>
      <PageActions>
        <button type="button" className="primary" onClick={() => setAdding(true)}>
          <Icon name="plus" size={16} strokeWidth={2.2} />
          Новый тип
        </button>
      </PageActions>

      <div className="toolbar">
        <Segmented
          value={view}
          onChange={setView}
          options={[
            ...(canReview || pendingTotal > 0 ? [{ value: "pending" as View, label: "Ждут проверки", count: pendingTotal }] : []),
            { value: "all" as View, label: "Все типы" },
            { value: "templates" as View, label: "Шаблоны поверки" },
            { value: "kits" as View, label: "Эталоны" },
          ]}
        />
        {view !== "templates" && view !== "kits" && (
          <SearchField className="grow" value={query} onChange={setQuery} placeholder="Название, номер в Госреестре, изготовитель" />
        )}
      </div>

      {view === "templates" ? (
        <VerificationTemplates canReview={canReview} />
      ) : view === "kits" ? (
        <StandardKits />
      ) : (
        <>
          {error && <Alert>{error}</Alert>}
          {loading && <Spinner />}
          {!loading && items.length === 0 && (
            view === "pending" && !query ? (
              <EmptyState icon="checkCircle" title="Всё проверено">
                Новые типы, которые заведут поверители или ассистент, появятся здесь.
              </EmptyState>
            ) : (
              <EmptyState icon="search" title="Ничего не нашлось">
                Попробуйте номер в Госреестре — например, 16078-13.
              </EmptyState>
            )
          )}

          {!loading && items.length > 0 && (
            <div className="card-list">
              {items.map((item) => (
                <div className="card type-row" key={item.id}>
                  <div className="grow">
                    <b>{item.name}</b>
                    {item.needs_review && <span className="pill warn" style={{ marginLeft: 8 }}>ждёт проверки</span>}
                    <div className="meta">
                      <span>№ <span className="cell-mono">{item.registry_number}</span></span>
                      <span>{item.manufacturer || "изготовитель не указан"}</span>
                      <span>МПИ {item.verification_interval_months} мес.</span>
                      {item.needs_review ? (
                        <span>завёл {item.created_by_name || "—"}</span>
                      ) : (
                        item.reviewed_by_name && <span><Icon name="check" size={12} /> проверил {item.reviewed_by_name}</span>
                      )}
                      <TemplateBadge item={item} />
                    </div>
                  </div>
                  <div className="row">
                    {canReview && item.needs_review && (
                      <button type="button" className="primary sm" disabled={busy === item.id} onClick={() => void confirm(item)}>
                        <Icon name="check" size={14} /> Всё верно
                      </button>
                    )}
                    {(canReview || item.needs_review) && (
                      <button type="button" className="sm" onClick={() => setOpen(item)}>
                        Открыть
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <Modal
        open={adding}
        onClose={closeAdding}
        size="lg"
        title="Новый тип СИ"
        subtitle={draft ? "Данные — из Госреестра, сверьте и сохраните" : "Начните вводить что угодно — подсказки из Госреестра (ФИФ)"}
      >
        {draft ? (
          <SiTypeForm initial={draft} onSaved={saved} onCancel={() => setDraft(null)} />
        ) : (
          adding && (
            <FifTypeSearch
              autoFocus
              onPickFif={setDraft}
              onPickLocal={(item) => {
                closeAdding();
                setView("all");
                setQuery(item.registry_number);
              }}
            />
          )
        )}
      </Modal>
      <Modal
        open={open != null}
        onClose={() => setOpen(null)}
        size="lg"
        title={open?.name}
        subtitle={open ? `№ ${open.registry_number}${open.needs_review ? ` · ждёт проверки, завёл ${open.created_by_name || "—"}` : ""}` : undefined}
      >
        {open && <SiTypeForm key={open.id} existing={open} onSaved={saved} onCancel={() => setOpen(null)} />}
      </Modal>
    </div>
  );
}

/** Короткая отметка в строке справочника: как дела с шаблоном поверки. */
function TemplateBadge({ item }: { item: SiType }) {
  const job = item.template_job;
  const template = item.templates?.[0];
  if (job && (job.status === "queued" || job.status === "running")) {
    return <span><Icon name="sparkle" size={12} /> ассистент готовит шаблон…</span>;
  }
  if (template) {
    return <span>шаблон {template.needs_review ? "ждёт проверки" : "есть"}</span>;
  }
  if (job?.status === "failed") return <span className="warn-text">шаблон не получился</span>;
  if (item.calculator_key === "water_meter") return null;
  return <span>без шаблона</span>;
}
