/** Режим 8 «Шаблоны аттестации» — шаблоны планов аттестации ИО по виду оборудования (ГОСТ Р 8.568-2017).
 *
 *  Вкладка «Шаблоны»: виды ИО (холодильник, термостат, сушильный шкаф, климатическая камера…) с их
 *  шаблонами → новый: документы → ассистент собирает план → сверка формул с примером → правка плана
 *  таблицами → «Проверен» (метролог) → «Применить к новому ИО». Вкладка «Аттестации ИО» — сами
 *  аттестации конкретного оборудования (ввод измерений, расчёт, подпись, протокол) — components/Attestations.
 *
 *  Действия: new, open-program (id шаблона), open-attestation (id аттестации), new-attestation. */

import { useCallback, useEffect, useMemo, useState } from "react";
import "../si-templates/si-templates.css";
import "./att-templates.css";
import Attestations from "../../components/Attestations";
import { scoreText } from "../../ui/CommandPalette";
import { EmptyState } from "../../ui/EmptyState";
import Icon from "../../ui/Icon";
import { SkeletonList } from "../../ui/Skeleton";
import { SearchField, Segmented } from "../../ui/controls";
import { usePersistent } from "../../ui/hooks";
import { useModeAction } from "../../ui/modeActions";
import { withViewTransition } from "../../ui/transition";
import type { ModeProps } from "../types";
import { EQUIPMENT_KINDS, fetchPrograms, kindKey, type Program } from "./api";
import NewAttTemplate from "./NewAttTemplate";
import ProgramView from "./ProgramView";

type View = { kind: "list" } | { kind: "new"; equipment?: string } | { kind: "program"; id: number };
const TABS = ["templates", "attestations"] as const;
type TabKey = (typeof TABS)[number];
const FILTERS = ["all", "draft", "reviewed"] as const;
type Filter = (typeof FILTERS)[number];

export default function AttTemplatesMode({ me, isReviewer, refreshCounts }: ModeProps) {
  const [tab, setTabRaw] = usePersistent<TabKey>("ei_doc_att_tab", "templates", TABS);
  const [view, setViewRaw] = useState<View>({ kind: "list" });
  const [programs, setPrograms] = useState<Program[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [showOff, setShowOff] = useState(false);
  const [openAttestation, setOpenAttestation] = useState<{ id: number | null; create?: boolean; seq: number }>({ id: null, seq: 0 });
  const canEdit = !!me && me.role !== "observer";

  const setView = (v: View) => withViewTransition(() => setViewRaw(v));
  const setTab = (t: TabKey) => withViewTransition(() => setTabRaw(t));

  const load = useCallback(async () => {
    setError("");
    try {
      setPrograms(await fetchPrograms({ all: true, plan: false }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => void load(), [load]);

  const changed = useCallback(() => {
    void load();
    refreshCounts();
  }, [load, refreshCounts]);

  const showAttestation = (id: number | null, create = false) => {
    setOpenAttestation((s) => ({ id, create, seq: s.seq + 1 }));
    setTab("attestations");
  };

  useModeAction("att-templates", {
    new: () => {
      setTab("templates");
      setView({ kind: "new" });
    },
    "open-program": (id) => {
      if (typeof id !== "number") return;
      setTab("templates");
      setView({ kind: "program", id });
    },
    "open-attestation": (id) => typeof id === "number" && showAttestation(id),
    "new-attestation": () => showAttestation(null, true),
  });

  const counts = useMemo(() => {
    const active = programs.filter((p) => p.is_active);
    return { all: active.length, draft: active.filter((p) => p.needs_review).length, reviewed: active.filter((p) => !p.needs_review).length };
  }, [programs]);

  const groups = useMemo(() => {
    const q = query.trim();
    const list = programs
      .filter((p) => showOff || p.is_active)
      .filter((p) => filter === "all" || (filter === "draft" ? p.needs_review : !p.needs_review))
      .filter((p) => !q || scoreText(`${p.title} ${p.equipment_kind} ${p.note}`, q) > 0);
    const map = new Map<string, { kind: string; items: Program[] }>();
    for (const p of list) {
      const key = kindKey(p.equipment_kind || "без вида");
      const g = map.get(key) ?? { kind: p.equipment_kind || "Без вида", items: [] };
      g.items.push(p);
      map.set(key, g);
    }
    return Array.from(map.values()).sort((a, b) => a.kind.localeCompare(b.kind, "ru"));
  }, [programs, filter, query, showOff]);

  const missingKinds = useMemo(() => {
    const have = new Set(programs.filter((p) => p.is_active).map((p) => kindKey(p.equipment_kind)));
    return EQUIPMENT_KINDS.filter((k) => ![...have].some((h) => h.includes(kindKey(k).split(" ")[0])));
  }, [programs]);

  const tabs = (
    <Segmented<TabKey>
      value={tab}
      onChange={setTab}
      options={[
        { value: "templates", label: "Шаблоны по видам ИО", icon: "layers", count: isReviewer ? counts.draft : undefined },
        { value: "attestations", label: "Аттестации ИО", icon: "shield" },
      ]}
    />
  );

  if (tab === "attestations") {
    return (
      <div className="att-mode">
        <div className="att-tabs">{tabs}</div>
        <Attestations
          key={openAttestation.seq}
          me={me}
          initialOpenId={openAttestation.id}
          initialCreate={openAttestation.create}
          onProgramCreated={(id) => {
            changed();
            setTab("templates");
            setView({ kind: "program", id });
          }}
        />
      </div>
    );
  }

  let body;
  if (view.kind === "new") {
    body = <NewAttTemplate initialKind={view.equipment} onCancel={() => setView({ kind: "list" })} onCreated={(id) => { changed(); setView({ kind: "program", id }); }} />;
  } else if (view.kind === "program") {
    body = (
      <ProgramView
        key={view.id}
        id={view.id}
        isReviewer={isReviewer}
        canEdit={canEdit}
        onBack={() => setView({ kind: "list" })}
        onChanged={changed}
        onApplied={(attId) => showAttestation(attId)}
      />
    );
  } else {
    body = (
      <div className="att-page">
        <div className="si-list-head">
          <h2>Шаблоны аттестации по видам ИО</h2>
          <p className="muted">
            Шаблон — план аттестации вида оборудования: точки, режимы, характеристики с формулами и допуски с источником.
            Собирает ассистент из примера протокола, программы и РЭ, формулы сверяются на числах примера, метролог
            проверяет. Дальше шаблон применяется к каждому новому ИО этого вида.
          </p>
        </div>
        <div className="toolbar si-list-tools">
          <span className="grow"><SearchField value={query} onChange={setQuery} placeholder="Вид оборудования, название шаблона" /></span>
          <Segmented<Filter>
            size="sm"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: "Все" },
              { value: "draft", label: "На проверке", count: counts.draft },
              { value: "reviewed", label: "Проверены" },
            ]}
          />
          <label className="sub att-off"><input type="checkbox" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} /> выключенные</label>
        </div>
        {isReviewer && counts.draft > 0 && filter !== "draft" && (
          <button type="button" className="si-review-banner" onClick={() => setFilter("draft")}>
            <Icon name="clock" size={16} />
            <span className="grow"><b>Ждут вашей проверки: {counts.draft}</b><span className="sub"> — планы аттестаций по ним тоже ждут проверки</span></span>
            <Icon name="arrowRight" size={15} />
          </button>
        )}
        {error ? (
          <EmptyState tone="error" title="Шаблоны не загрузились" action={{ label: "Повторить", icon: "refresh", onClick: () => { setLoading(true); void load(); } }}>{error}</EmptyState>
        ) : loading && !programs.length ? (
          <SkeletonList rows={4} label="Загружаю шаблоны аттестации" />
        ) : !programs.length ? (
          <EmptyState icon="shield" title="Шаблонов аттестации пока нет" action={{ label: "Новый шаблон", icon: "plus", onClick: () => setView({ kind: "new" }), hotkey: "n" }}>
            Загрузите пример протокола, программу аттестации или РЭ — ассистент соберёт план, EI_doc сверит формулы с примером.
          </EmptyState>
        ) : !groups.length ? (
          <EmptyState compact icon="search" title="Ничего не нашлось" secondary={{ label: "Сбросить фильтр", onClick: () => { setFilter("all"); setQuery(""); } }} />
        ) : (
          <div className="att-kinds">
            {groups.map((g) => (
              <section key={g.kind} className="att-kind card">
                <div className="att-kind-head">
                  <span className="si-row-icon"><Icon name="thermometer" size={16} /></span>
                  <b className="grow">{g.kind}</b>
                  {canEdit && (
                    <button type="button" className="sm" onClick={() => setView({ kind: "new", equipment: g.kind })}>
                      <Icon name="plus" size={14} /> Ещё шаблон
                    </button>
                  )}
                </div>
                <div className="si-rows">
                  {g.items.map((p) => (
                    <button key={p.id} type="button" className={`si-row ${p.needs_review ? "review" : ""}`} onClick={() => setView({ kind: "program", id: p.id })}>
                      <span className="si-row-main">
                        <b>{p.title}</b>
                        <span className="sub">
                          {[p.created_via_assistant ? "собрал ассистент" : p.source_attestation_id ? "по проведённой аттестации" : p.created_by && `завёл ${p.created_by}`,
                            p.used ? `применён ${p.used} раз` : "ещё не применялся"].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                      {!p.is_active && <span className="pill muted">выключен</span>}
                      <span className={`pill ${p.needs_review ? "warn" : "ok"}`}>{p.needs_review ? "на проверке" : "проверен"}</span>
                      <Icon name="chevronRight" size={16} className="si-row-go" />
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
        {canEdit && !loading && missingKinds.length > 0 && (
          <div className="att-missing-kinds">
            <span className="sub">Ещё нет шаблона:</span>
            {missingKinds.map((k) => (
              <button key={k} type="button" className="chip" onClick={() => setView({ kind: "new", equipment: k })}>+ {k}</button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="att-mode">
      {view.kind === "list" && <div className="att-tabs">{tabs}</div>}
      {body}
    </div>
  );
}
