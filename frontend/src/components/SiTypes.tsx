import { useCallback, useEffect, useRef, useState } from "react";
import { confirmSiType, deleteSiType, fetchSiTypes, type Me, type SiType, type SiTypeSuggestion } from "../api";
import { refreshSiTypeCache, rememberSiType, searchSiTypes } from "../offline/sync";
import Icon from "../ui/Icon";
import { confirmDialog, Modal } from "../ui/Modal";
import toast from "../ui/Toast";
import { Alert, EmptyState, PageActions, SearchField, Segmented, Spinner } from "../ui/controls";
import SiTypeDocuments from "./FifDocuments";
import FifTypeCard from "./FifTypeCard";
import FifTypeSearch from "./FifTypeSearch";
import RstStatus from "./RstStatus";
import SiTypeForm, { loadPresets, type SiTypeDraft } from "./SiTypeForm";
import StandardKits from "./StandardKits";
import VerificationTemplates from "./VerificationTemplates";

const REVIEWER_ROLES = new Set(["owner", "head", "metrologist"]);

type View = "pending" | "all" | "templates" | "kits";

type FifState = "idle" | "loading" | "done" | "offline" | "unavailable";

/**
 * Справочник типов СИ — вместо раздела в админке.
 *
 * Метролог открывает «Ждут проверки», сверяет характеристики с описанием типа
 * в Госреестре, при необходимости правит и подтверждает. Карточка типа — в окне.
 *
 * Поиск в «Все типы» — как при заведении прибора: сразу и по своим типам, и по
 * Госреестру (ФИФ). Карточка из реестра открывается для справки — модификации,
 * свидетельство, МПИ, методика и описание типа; завести тип оттуда — одной кнопкой.
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
  // Госреестр: результаты поиска и открытая справочная карточка.
  const [fif, setFif] = useState<SiTypeSuggestion[]>([]);
  const [fifState, setFifState] = useState<FifState>("idle");
  const [fifNote, setFifNote] = useState("");
  const [fifOpen, setFifOpen] = useState<{ ident: string; title: string; query: string } | null>(null);
  const fifRun = useRef(0);

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

  // Поиск в Госреестре — параллельно со своим справочником и не задерживая его.
  useEffect(() => {
    const q = query.trim();
    const run = ++fifRun.current;
    if (view !== "all" || q.length < 2) {
      setFif([]);
      setFifState("idle");
      return;
    }
    setFifState("loading");
    const timer = setTimeout(async () => {
      const found = await searchSiTypes(q);
      if (run !== fifRun.current) return;
      setFif(found.results.filter((item) => item.si_type_id === null));
      setFifNote(found.error ?? "");
      setFifState(found.offline ? "offline" : found.fifUnavailable ? "unavailable" : "done");
    }, 350);
    return () => clearTimeout(timer);
  }, [query, view]);

  const openLocal = async (id: number) => {
    setFifOpen(null);
    const known = items.find((item) => item.id === id);
    if (known) return setOpen(known);
    try {
      const list = await fetchSiTypes({ q: "" });
      const found = list.find((item) => item.id === id);
      if (found) setOpen(found);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось открыть тип");
    }
  };

  const remove = async (item: SiType) => {
    const ok = await confirmDialog({
      title: `Удалить тип «${item.name}»?`,
      text:
        "Если по типу уже есть приборы, он не сотрётся, а скроется из справочника и поиска — прошлые поверки " +
        "и протоколы останутся как были. Тип без приборов удалится вместе со своим шаблоном поверки.",
      danger: true,
    });
    if (!ok) return;
    setBusy(item.id);
    setError("");
    try {
      const reply = await deleteSiType(item.id);
      toast.success(reply?.archived ? "Тип скрыт из справочника" : "Тип удалён", reply?.detail ? { description: reply.detail } : undefined);
      setOpen(null);
      void refreshSiTypeCache();
      await load();
      onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось удалить тип");
    } finally {
      setBusy(null);
    }
  };

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
        <RstStatus />
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
          <SearchField
            className="grow"
            value={query}
            onChange={setQuery}
            placeholder={view === "all" ? "Название, номер, изготовитель — ищу у нас и в Госреестре" : "Название, номер в Госреестре, изготовитель"}
          />
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
          {!loading && items.length === 0 && !(view === "all" && (fif.length > 0 || fifState === "loading")) && (
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
                    {item.calculator_key !== "water_meter" && (
                      <button type="button" className="sm" title="Чат с ассистентом и протокол по шаблону рядом"
                        onClick={() => (window.location.hash = `#/si-templates/${item.id}`)}>
                        <Icon name="layers" size={14} /> Шаблон протокола
                      </button>
                    )}
                    {canReview && item.needs_review && (
                      <button type="button" className="primary sm" disabled={busy === item.id} onClick={() => void confirm(item)}>
                        <Icon name="check" size={14} /> Всё верно
                      </button>
                    )}
                    <button type="button" className="sm" onClick={() => setOpen(item)}>
                      Открыть
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {view === "all" && fifState !== "idle" && (
            <section className="fif-results">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", margin: "18px 4px 8px" }}>
                <h3 style={{ margin: 0 }}>В Госреестре</h3>
                {fifState === "loading" && <span className="hint">Ищу в ФИФ…</span>}
                {fifState === "done" && <span className="hint">{fif.length ? `нашлось: ${fif.length}` : "ничего нового"}</span>}
              </div>
              {fifState === "offline" && <p className="hint warn-text">Нет связи — в Госреестре поискать не получится.</p>}
              {fifState === "unavailable" && (
                <p className="hint warn-text">
                  Госреестр (ФИФ) сейчас не отвечает — попробуйте чуть позже.
                  {fifNote && <span className="sub" style={{ display: "block" }}>{fifNote}</span>}
                </p>
              )}
              {fif.length > 0 && (
                <div className="pick-list">
                  {fif.map((item) => (
                    <button
                      type="button"
                      key={`f-${item.registry_number}-${item.manufacturer}-${item.fif_uuid}`}
                      className="pick-item"
                      onClick={() =>
                        setFifOpen({
                          ident: item.fif_uuid || item.registry_number,
                          title: item.notation || item.name,
                          query: query.trim(),
                        })
                      }
                    >
                      <b>{item.notation || item.name}</b>
                      {item.title && <span className="sub">{item.title}</span>}
                      <span className="sub">
                        № {item.registry_number} · {item.manufacturer || "изготовитель не указан"}
                        {item.interval_months ? ` · МПИ ${item.interval_months} мес.` : ""}
                      </span>
                      {item.ambiguous && <span className="pill warn">такое название у нескольких — сверьте изготовителя</span>}
                    </button>
                  ))}
                </div>
              )}
            </section>
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
        {open && (canReview || open.needs_review ? (
          <>
            <SiTypeForm key={open.id} existing={open} onSaved={saved} onCancel={() => setOpen(null)} />
            <div className="row" style={{ justifyContent: "space-between", marginTop: 12, gap: 8, flexWrap: "wrap" }}>
              {open.fif_uuid ? (
                <button type="button" className="sm ghost" onClick={() => setFifOpen({ ident: open.fif_uuid, title: open.name, query: open.name })}>
                  <Icon name="book" size={14} /> Карточка в Госреестре
                </button>
              ) : <span />}
              <button type="button" className="sm ghost danger" disabled={busy === open.id} onClick={() => void remove(open)}>
                <Icon name="trash" size={14} /> Удалить тип
              </button>
            </div>
          </>
        ) : (
          <SiTypeInfo item={open} onFif={() => setFifOpen({ ident: open.fif_uuid || open.registry_number, title: open.name, query: open.name })} />
        ))}
      </Modal>
      <Modal
        open={fifOpen != null}
        onClose={() => setFifOpen(null)}
        size="lg"
        title={fifOpen?.title}
        subtitle="Карточка типа в Госреестре (ФИФ)"
      >
        {fifOpen && (
          <FifTypeCard
            ident={fifOpen.ident}
            query={fifOpen.query}
            onOpenLocal={(id) => void openLocal(id)}
            onAdd={(next) => {
              setFifOpen(null);
              setOpen(null);
              setDraft(next);
              setAdding(true);
            }}
          />
        )}
      </Modal>
    </div>
  );
}

/** Тип для справки — поверителю, который не может править проверенный тип. */
function SiTypeInfo({ item, onFif }: { item: SiType; onFif: () => void }) {
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    loadPresets()
      .then((info) => setLabels(Object.fromEntries(info.fields.map((f) => [f.key, f.label]))))
      .catch(() => undefined);
  }, []);
  const limits = Object.entries(item.limits ?? {});
  return (
    <div>
      {item.type_title && <p className="hint" style={{ marginTop: 0 }}>{item.type_title}</p>}
      <div className="kv">
        <div><span className="k">Номер в Госреестре</span><span className="v cell-mono">{item.registry_number}</span></div>
        <div><span className="k">Изготовитель</span><span className="v">{item.manufacturer || "не указан"}</span></div>
        <div><span className="k">Вид СИ</span><span className="v">{item.family_name}</span></div>
        <div><span className="k">МПИ</span><span className="v">{item.verification_interval_months} мес.</span></div>
        {item.certificate_valid_to && (
          <div><span className="k">Свидетельство до</span><span className="v">{new Date(item.certificate_valid_to).toLocaleDateString("ru-RU")}</span></div>
        )}
        {item.reviewed_by_name && <div><span className="k">Проверил</span><span className="v">{item.reviewed_by_name}</span></div>}
      </div>
      {limits.length > 0 && (
        <div className="kv">
          {limits.map(([key, value]) => (
            <div key={key}><span className="k">{labels[key] ?? key}</span><span className="v cell-mono">{String(value)}</span></div>
          ))}
        </div>
      )}
      {item.fif_uuid && <SiTypeDocuments siTypeId={item.id} />}
      <div className="actions" style={{ marginTop: 12 }}>
        <button type="button" onClick={onFif}><Icon name="book" size={15} /> Карточка в Госреестре</button>
      </div>
      <p className="hint">Правит проверенный тип метролог — если что-то не так, напишите ему.</p>
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
