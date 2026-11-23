/** Список контрактов: номер ЕИС крупно, заказчик, закон, срок, план/факт полосой, заходы. */

import { useEffect, useMemo, useRef, useState } from "react";
import { fetchProcurements, type ProcurementRow } from "../../api";
import { daysUntil, errorText, ruShort } from "../../components/tripUtil";
import { EmptyState } from "../../ui/EmptyState";
import Icon from "../../ui/Icon";
import { SkeletonList } from "../../ui/Skeleton";
import { PageActions, SearchField, Segmented } from "../../ui/controls";
import { normalize } from "../../ui/CommandPalette";

type State = "open" | "closed";
type Law = "all" | "44" | "223" | "other";

const LAW_LABEL: Record<string, string> = { "44": "44-ФЗ", "223": "223-ФЗ", direct: "Договор", other: "Другое" };

export function deadlineTone(deadline: string | null, closed: boolean): "" | "warn" | "bad" {
  const left = daysUntil(deadline);
  if (closed || left == null) return "";
  if (left < 0) return "bad";
  return left <= 21 ? "warn" : "";
}

export function DeadlineText({ deadline, closed }: { deadline: string | null; closed: boolean }) {
  const left = daysUntil(deadline);
  if (!deadline) return <span className="pr-deadline muted">срок не указан</span>;
  const tone = deadlineTone(deadline, closed);
  return (
    <span className={`pr-deadline ${tone}`}>
      до <span className="data">{ruShort(deadline)}</span>
      {!closed && left != null && (left < 0 ? " · истёк" : left <= 21 ? ` · ${left} дн.` : "")}
    </span>
  );
}

export function PlanBar({ planned, ready, entered }: { planned: number; ready: number; entered: number }) {
  if (planned <= 0) {
    return <div className="pr-plan"><span className="data">{ready}</span> <small>поверено · план не задан</small></div>;
  }
  const done = Math.min(100, (ready / planned) * 100);
  const inWork = Math.min(100 - done, (Math.max(0, entered - ready) / planned) * 100);
  return (
    <div className="pr-plan" title={`Поверено ${ready} из ${planned}, внесено ${entered}`}>
      <div className="pr-plan-nums"><span className="data">{ready}</span><small> / {planned}</small></div>
      <div className="pr-bar"><i className="done" style={{ width: `${done}%` }} /><i className="work" style={{ width: `${inWork}%` }} /></div>
    </div>
  );
}

export default function ProcurementList({
  canCreate, onOpen, onCreate, version,
}: {
  canCreate: boolean;
  onOpen: (id: number) => void;
  onCreate: () => void;
  version: number;
}) {
  const [state, setState] = useState<State>("open");
  const [law, setLaw] = useState<Law>("all");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<ProcurementRow[] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setRows(null);
    setError("");
    fetchProcurements({ state })
      .then(setRows)
      .catch((exc) => setError(errorText(exc, "Не удалось загрузить закупки")));
  }, [state, version, retry]);

  const shown = useMemo(() => {
    const q = normalize(query);
    return (rows ?? [])
      .filter((row) => law === "all" || (law === "other" ? !["44", "223"].includes(row.law) : row.law === law))
      .filter((row) => !q || normalize(`${row.number} ${row.label} ${row.customer} ${row.title} ${row.contract_number}`).includes(q))
      .sort((a, b) => (a.deadline ?? "9999").localeCompare(b.deadline ?? "9999"));
  }, [rows, law, query]);

  const count = (value: Law) => (rows ?? []).filter((row) => value === "all" || (value === "other" ? !["44", "223"].includes(row.law) : row.law === value)).length;

  const onListKey = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>(".pr-row") ?? []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = items[Math.max(0, Math.min(items.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      event.preventDefault();
      next.focus();
    }
  };

  return (
    <div className="pr-list">
      {canCreate && (
        <PageActions>
          <button type="button" className="primary" onClick={onCreate}>
            <Icon name="plus" size={16} /> Закупка
          </button>
        </PageActions>
      )}
      <div className="toolbar pr-toolbar">
        <Segmented
          value={state}
          onChange={setState}
          options={[{ value: "open", label: "Исполняются" }, { value: "closed", label: "Архив" }]}
        />
        <Segmented
          value={law}
          onChange={setLaw}
          size="sm"
          options={[
            { value: "all", label: "Все", count: count("all") },
            { value: "44", label: "44-ФЗ", count: count("44") },
            { value: "223", label: "223-ФЗ", count: count("223") },
            { value: "other", label: "Другое", count: count("other") },
          ]}
        />
        <SearchField value={query} onChange={setQuery} placeholder="Номер, заказчик, предмет" className="grow" />
      </div>

      {error && <EmptyState tone="error" title="Список не загрузился" action={{ label: "Повторить", onClick: () => setRetry((n) => n + 1) }}>{error}</EmptyState>}
      {!error && rows == null && <SkeletonList rows={5} />}
      {rows != null && shown.length === 0 && (
        query || law !== "all" ? (
          <EmptyState icon="search" title="Ничего не нашлось" compact>Попробуйте другой номер или название заказчика.</EmptyState>
        ) : (
          <EmptyState
            icon="contract"
            title={state === "open" ? "Контрактов в работе нет" : "Архив пуст"}
            action={canCreate && state === "open" ? { label: "Завести закупку", icon: "plus", onClick: onCreate, hotkey: "N" } : undefined}
          >
            {state === "open"
              ? "Заведите закупку по номеру из ЕИС: документы, пропуска и заходы будут собраны в одной карточке."
              : "Сюда попадают исполненные и расторгнутые контракты."}
          </EmptyState>
        )
      )}

      {shown.length > 0 && (
        <div className="pr-rows" ref={listRef} onKeyDown={onListKey}>
          {shown.map((row) => {
            const closed = row.status === "done" || row.status === "cancelled";
            return (
              <button type="button" key={row.id} className={`pr-row card tap ${deadlineTone(row.deadline, closed)}`} onClick={() => onOpen(row.id)}>
                <div className="pr-row-main">
                  <div className="pr-number data">{row.number || row.contract_number || row.label}</div>
                  <div className="pr-customer">{row.customer}</div>
                  <div className="sub pr-title">{row.title}</div>
                </div>
                <div className="pr-row-meta">
                  <span className="pill muted plain">{LAW_LABEL[row.law] ?? row.law_display}</span>
                  {row.status !== "active" && <span className={`pill ${row.status === "cancelled" ? "bad" : "muted"}`}>{row.status_display}</span>}
                  <DeadlineText deadline={row.deadline} closed={closed} />
                  <span className="sub">{row.trips_total ? `заходов: ${row.trips_total}` : "заходов нет"}</span>
                </div>
                <PlanBar planned={row.progress.planned} ready={row.progress.ready} entered={row.progress.entered} />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
