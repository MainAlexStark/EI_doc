/** Список шаблонов поверки: мои рабочие места + действующие шаблоны остальных; фильтр по статусу, поиск,
 *  «на проверке» со счётчиком для метролога. */

import { useMemo, useState } from "react";
import type { VerificationTemplate } from "../../api";
import type { TemplateWorkspace } from "../../assistant/api";
import { scoreText } from "../../ui/CommandPalette";
import { EmptyState } from "../../ui/EmptyState";
import Icon from "../../ui/Icon";
import { SkeletonList } from "../../ui/Skeleton";
import { Alert, SearchField, Segmented } from "../../ui/controls";
import { usePersistent } from "../../ui/hooks";
import { STATUS_LABEL, STATUS_PILL, activeBySiType, templateStatus, workspaceStatus, type TplStatus } from "./api";

export type ListRow = {
  key: string;
  name: string;
  number: string;
  extra: string;
  status: TplStatus;
  updated: string;
  workspace?: TemplateWorkspace;
  template?: VerificationTemplate;
  busy?: boolean;
  mine: boolean;
};

const FILTERS = ["all", "draft", "review", "reviewed"] as const;
type Filter = (typeof FILTERS)[number];

export function buildRows(workspaces: TemplateWorkspace[], templates: VerificationTemplate[]): ListRow[] {
  const active = activeBySiType(templates);
  const rows: ListRow[] = workspaces.map((w) => {
    const t = active.get(w.si_type.id);
    return {
      key: `w${w.id}`,
      name: w.si_type.name,
      number: w.si_type.registry_number,
      extra: [w.si_type.manufacturer, w.has_draft ? `черновик, ред. ${w.revision}` : ""].filter(Boolean).join(" · "),
      status: workspaceStatus(w, t),
      updated: w.updated_at,
      workspace: w,
      template: t,
      busy: w.agent_busy,
      mine: true,
    };
  });
  const mineTypes = new Set(workspaces.map((w) => w.si_type.id));
  for (const t of templates) {
    if (!t.is_active || (t.si_type != null && mineTypes.has(t.si_type))) continue;
    rows.push({
      key: `t${t.id}`,
      name: t.si_type_name || t.title,
      number: "",
      extra: [t.si_type ? t.title : `все типы вида «${t.family_name}»`, t.created_by ? `завёл ${t.created_by}` : ""].filter(Boolean).join(" · "),
      status: templateStatus(t),
      updated: t.updated_at,
      template: t,
      mine: false,
    });
  }
  return rows.sort((a, b) => (a.status === "review" ? 0 : 1) - (b.status === "review" ? 0 : 1) || (b.updated > a.updated ? 1 : -1));
}

export default function TemplateList({
  rows, loading, error, isReviewer, onRetry, onNew, onOpen,
}: {
  rows: ListRow[];
  loading: boolean;
  error: string;
  isReviewer: boolean;
  onRetry: () => void;
  onNew: () => void;
  onOpen: (row: ListRow) => void;
}) {
  const [filter, setFilter] = usePersistent<Filter>("ei_doc_si_tpl_filter", "all", FILTERS);
  const [query, setQuery] = useState("");

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: rows.length, draft: 0, review: 0, reviewed: 0 };
    rows.forEach((r) => (c[r.status] += 1));
    return c;
  }, [rows]);

  const shown = useMemo(() => {
    const q = query.trim();
    return rows
      .filter((r) => filter === "all" || r.status === filter)
      .map((r) => ({ r, score: q ? scoreText(`${r.name} ${r.number} ${r.extra}`, q) : 1 }))
      .filter((x) => x.score > 0)
      .sort((a, b) => (q ? b.score - a.score : 0))
      .map((x) => x.r);
  }, [rows, filter, query]);

  return (
    <div className="si-list">
      <div className="si-list-head">
        <div>
          <h2>Шаблоны протоколов поверки</h2>
          <p className="muted">
            Шаблон — как поверять тип СИ по его методике и как выглядит протокол. Собирает ассистент вместе с вами, метролог
            проверяет, дальше по типу создаются протоколы.
          </p>
        </div>
      </div>
      <div className="toolbar si-list-tools">
        <span className="grow">
          <SearchField value={query} onChange={setQuery} placeholder="Тип СИ, номер в Госреестре, изготовитель" />
        </span>
        <Segmented<Filter>
          size="sm"
          value={filter}
          onChange={setFilter}
          ariaLabel="Статус"
          options={[
            { value: "all", label: "Все" },
            { value: "draft", label: "Черновики", count: counts.draft },
            { value: "review", label: "На проверке", count: counts.review, icon: isReviewer ? "clock" : undefined },
            { value: "reviewed", label: "Проверены" },
          ]}
        />
      </div>

      {isReviewer && counts.review > 0 && filter !== "review" && (
        <button type="button" className="si-review-banner" onClick={() => setFilter("review")}>
          <Icon name="clock" size={16} />
          <span className="grow">
            <b>Ждут вашей проверки: {counts.review}</b>
            <span className="sub"> — протоколы по ним не подписываются, пока вы не проверите</span>
          </span>
          <Icon name="arrowRight" size={15} />
        </button>
      )}

      {error ? (
        <EmptyState tone="error" title="Список шаблонов не загрузился" action={{ label: "Повторить", icon: "refresh", onClick: onRetry }}>
          {error}
        </EmptyState>
      ) : loading && rows.length === 0 ? (
        <SkeletonList rows={5} label="Загружаю шаблоны" />
      ) : rows.length === 0 ? (
        <EmptyState icon="layers" title="Шаблонов пока нет" action={{ label: "Новый шаблон", icon: "plus", onClick: onNew, hotkey: "n" }}>
          Найдите тип СИ в Госреестре — ассистент прочитает методику поверки и соберёт шаблон протокола рядом с чатом.
        </EmptyState>
      ) : shown.length === 0 ? (
        <EmptyState compact icon="search" title="Ничего не нашлось" secondary={{ label: "Сбросить фильтр", onClick: () => { setFilter("all"); setQuery(""); } }}>
          {query ? `По «${query}» шаблонов нет.` : `Шаблонов со статусом «${STATUS_LABEL[filter as TplStatus]}» нет.`}
        </EmptyState>
      ) : (
        <div className="si-rows" role="list">
          {shown.map((r) => (
            <button key={r.key} type="button" role="listitem" className={`si-row ${r.status}`} onClick={() => onOpen(r)}>
              <span className="si-row-icon"><Icon name={r.mine ? "sparkle" : "layers"} size={16} /></span>
              <span className="si-row-main">
                <b>{r.name}</b>
                <span className="sub">
                  {r.number && <span className="data">№ {r.number}</span>}
                  {r.number && r.extra ? " · " : ""}
                  {r.extra}
                </span>
              </span>
              {r.busy && <span className="spinner sm" title="Ассистент работает" />}
              <span className={`pill ${STATUS_PILL[r.status]}`}>{STATUS_LABEL[r.status]}</span>
              <Icon name="chevronRight" size={16} className="si-row-go" />
            </button>
          ))}
        </div>
      )}
      {!loading && !error && rows.length > 0 && (
        <Alert tone="info">
          Протоколы разных типов СИ оформлены одинаково (как у счётчиков воды), меняется наполнение — его задаёт методика.
          Счётчики воды поверяет встроенный калькулятор, шаблон им не нужен.
        </Alert>
      )}
    </div>
  );
}
