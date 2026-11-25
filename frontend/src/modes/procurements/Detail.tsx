/** Карточка закупки: шапка с ключевым и вкладки «Выжимка», «Документы», «Пропуска», «Заходы». */

import { useCallback, useEffect, useState } from "react";
import { deleteProcurement, fetchProcurement, type Procurement, type ProcurementPass } from "../../api";
import { PassModal, ProcurementForm } from "../../components/Procurements";
import { errorText, ruShort } from "../../components/tripUtil";
import { EmptyState } from "../../ui/EmptyState";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import { confirmDialog } from "../../ui/Modal";
import toast from "../../ui/Toast";
import { Skeleton, SkeletonText } from "../../ui/Skeleton";
import { PageActions, Segmented } from "../../ui/controls";
import { usePersistent } from "../../ui/hooks";
import type { Go } from "../types";
import AssistantSheet from "./AssistantSheet";
import DocumentsTab from "./Documents";
import { DeadlineText } from "./List";
import SummaryTab from "./Summary";
import VisitsTab from "./Visits";

type Tab = "summary" | "documents" | "passes" | "visits";
const TABS: Tab[] = ["summary", "documents", "passes", "visits"];

const money = (value: string | null) =>
  value ? `${Number(value).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽` : "";

const PASS_TONE: Record<ProcurementPass["status"], string> = { needed: "bad", requested: "warn", issued: "ok", refused: "bad" };

export default function ProcurementDetail({ id, go, onBack }: { id: number; go: Go; onBack: () => void }) {
  const [item, setItem] = useState<Procurement | null>(null);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [tab, setTab] = usePersistent<Tab>("ei_doc_procurement_tab", "summary", TABS);
  const [editing, setEditing] = useState(false);
  const [assistant, setAssistant] = useState(false);
  const [summaryVersion, setSummaryVersion] = useState(0);
  const [passEdit, setPassEdit] = useState<ProcurementPass | "new" | null>(null);

  const reload = useCallback(() => {
    setError("");
    fetchProcurement(id)
      .then(setItem)
      .catch((exc) => {
        if ((exc as { status?: number }).status === 404) setMissing(true);
        else setError(errorText(exc, "Не удалось загрузить закупку"));
      });
  }, [id]);

  useEffect(() => {
    setItem(null);
    setMissing(false);
    reload();
  }, [reload]);

  useHotkey("[", () => setTab(TABS[(TABS.indexOf(tab) + TABS.length - 1) % TABS.length]), { description: "Предыдущая вкладка закупки", group: "Закупки" });
  useHotkey("]", () => setTab(TABS[(TABS.indexOf(tab) + 1) % TABS.length]), { description: "Следующая вкладка закупки", group: "Закупки" });

  const back = (
    <button type="button" className="back" onClick={onBack}><Icon name="chevronLeft" size={20} /> Закупки</button>
  );
  if (missing) {
    return (
      <div className="pr-detail">
        {back}
        <EmptyState icon="contract" title="Закупка не найдена" action={{ label: "К списку", onClick: onBack }}>
          Возможно, её удалили или вы не участвуете в командировках по ней.
        </EmptyState>
      </div>
    );
  }
  if (error) {
    return (
      <div className="pr-detail">
        {back}
        <EmptyState tone="error" title="Закупка не загрузилась" action={{ label: "Повторить", onClick: reload }}>{error}</EmptyState>
      </div>
    );
  }
  if (!item) {
    return (
      <div className="pr-detail">
        {back}
        <Skeleton width="40%" height={34} />
        <div style={{ height: 12 }} />
        <SkeletonText lines={4} />
      </div>
    );
  }

  const canEdit = item.can_edit;
  const remove = async () => {
    const ok = await confirmDialog({
      title: "Удалить закупку?",
      text: `${item.label} — с документами, выжимкой и пропусками. Приёмки в счёт контракта останутся, ` +
        "но перестанут считаться в план-факт. Если по закупке есть командировки, сначала удалите их.",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteProcurement(item.id);
      toast.success("Закупка удалена");
      onBack();
    } catch (exc) {
      toast.error("Закупка не удалена", { description: errorText(exc, "Повторите позже") });
    }
  };
  const closed = item.status === "done" || item.status === "cancelled";
  const prompt = [
    `Собери выжимку по закупке id=${item.id} (${item.label}, заказчик ${item.customer}${item.customer_inn ? `, ИНН ${item.customer_inn}` : ""}).`,
    "Прочитай документы закупки (ei_procurement_documents, при необходимости ei_procurement_search) и предложи выжимку через ei_procurement_summary_propose:",
    "требования к исполнителю; перечень СИ с наименованием, типом, количеством и адресом объекта; сроки и этапы; пропуска и допуск; контакты заказчика; штрафы и неустойки; срок исполнения.",
    "У каждого пункта укажи источник (документ, пункт, страница). Если документ не прочитан (скан) — скажи об этом.",
  ].join(" ");

  return (
    <div className="pr-detail">
      {back}
      <PageActions>
        {item.open_url && (
          <a className="btn" href={item.open_url} target="_blank" rel="noreferrer"><Icon name="external" size={15} /> <span className="hide-phone">ЕИС</span></a>
        )}
        {canEdit && <button type="button" onClick={() => setEditing(true)}><Icon name="pen" size={15} /> <span className="hide-phone">Реквизиты</span></button>}
        {canEdit && (
          <button type="button" className="ghost danger" title="Удалить закупку" onClick={() => void remove()}>
            <Icon name="trash" size={15} /> <span className="hide-phone">Удалить</span>
          </button>
        )}
      </PageActions>

      <header className="pr-head">
        <div className="pr-head-main">
          <div className="pr-head-number data">{item.number || item.contract_number || item.label}</div>
          <h2>{item.customer}</h2>
          <div className="sub">{item.title}</div>
        </div>
        <dl className="pr-facts">
          <div><dt>Основание</dt><dd>{item.law_display}{item.status !== "active" && <> · <span className={item.status === "cancelled" ? "fail-text" : ""}>{item.status_display}</span></>}</dd></div>
          {item.customer_inn && <div><dt>ИНН</dt><dd className="data">{item.customer_inn}</dd></div>}
          {(item.contract_number || item.contract_date) && (
            <div><dt>Контракт</dt><dd>{item.contract_number && <span className="data">№ {item.contract_number}</span>}{item.contract_date && ` от ${ruShort(item.contract_date)}`}</dd></div>
          )}
          <div><dt>Срок</dt><dd><DeadlineText deadline={item.deadline} closed={closed} /></dd></div>
          {item.amount && <div><dt>Цена</dt><dd className="data">{money(item.amount)}</dd></div>}
          {item.responsible && <div><dt>Ответственный</dt><dd>{item.responsible.full_name}</dd></div>}
        </dl>
        {!canEdit && <div className="pr-readonly sub"><Icon name="eye" size={14} /> Только чтение — менять закупку могут руководитель и менеджер</div>}
      </header>

      <Segmented
        value={tab}
        onChange={setTab}
        className="pr-tabs"
        ariaLabel="Разделы закупки"
        options={[
          { value: "summary", label: "Выжимка", icon: "clipboard" },
          { value: "documents", label: "Документы", icon: "folder" },
          { value: "passes", label: "Пропуска", icon: "lock", count: item.passes.length || undefined },
          { value: "visits", label: "Заходы", icon: "car", count: item.trips.length || undefined },
        ]}
      />

      <div className="pr-tab-body">
        {tab === "summary" && (
          <SummaryTab procurementId={item.id} canEdit={canEdit} onAskAssistant={() => setAssistant(true)} version={summaryVersion} />
        )}
        {tab === "documents" && <DocumentsTab procurementId={item.id} canEdit={canEdit} />}
        {tab === "passes" && (
          <div className="pr-passes">
            {item.passes.length === 0 ? (
              <EmptyState
                icon="lock"
                title="Пропусков нет"
                compact
                action={canEdit ? { label: "Пропуск", icon: "plus", onClick: () => setPassEdit("new") } : undefined}
              >
                Если на объекты заказчика пускают по пропускам — заведите их: в командировке будет видно, у кого пропуск действует на даты поездки.
              </EmptyState>
            ) : (
              <ul className="pr-doc-list">
                {item.passes.map((pass) => (
                  <li key={pass.id} className="pr-doc">
                    <Icon name={pass.employee ? "user" : "car"} size={18} />
                    <span className="pr-doc-name">{pass.holder}</span>
                    <span className="sub pr-doc-meta">
                      {[
                        pass.number && `№ ${pass.number}`,
                        pass.valid_from || pass.valid_to ? [pass.valid_from && `с ${ruShort(pass.valid_from)}`, pass.valid_to && `по ${ruShort(pass.valid_to)}`].filter(Boolean).join(" ") : "",
                        pass.site || "все объекты",
                        pass.note,
                      ].filter(Boolean).join(" · ")}
                    </span>
                    <span className={`pill ${PASS_TONE[pass.status]}`}>{pass.status_display}</span>
                    {canEdit && (
                      <span className="pr-doc-actions">
                        <button type="button" className="icon-btn sm" aria-label="Изменить пропуск" onClick={() => setPassEdit(pass)}><Icon name="pen" size={14} /></button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {canEdit && item.passes.length > 0 && (
              <button type="button" onClick={() => setPassEdit("new")}><Icon name="plus" size={15} /> Пропуск</button>
            )}
            {item.sites.length > 0 && (
              <>
                <h3 className="pr-subhead">Объекты заказчика <span className="count">{item.sites.length}</span></h3>
                <ul className="pr-doc-list">
                  {item.sites.map((site) => (
                    <li key={site.id} className="pr-doc">
                      <Icon name="mapPin" size={17} />
                      <span className="pr-doc-name">{site.address}</span>
                      {site.is_restricted && <span className="pill warn plain">режимный</span>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
        {tab === "visits" && <VisitsTab item={item} canEdit={canEdit} go={go} onChanged={reload} />}
      </div>

      {editing && (
        <ProcurementForm
          item={item}
          onClose={() => setEditing(false)}
          onSaved={(fresh) => {
            setEditing(false);
            setItem(fresh);
          }}
        />
      )}
      {passEdit && (
        <PassModal
          procurement={item}
          pass={passEdit === "new" ? null : passEdit}
          onClose={() => setPassEdit(null)}
          onSaved={() => {
            setPassEdit(null);
            reload();
          }}
        />
      )}
      <AssistantSheet
        open={assistant}
        title={`Выжимка · ${item.label}`}
        subtitle="Ассистент читает документы и предложит запись — подтвердите карточкой"
        prompt={prompt}
        onClose={() => {
          setAssistant(false);
          setSummaryVersion((v) => v + 1);
          reload();
        }}
      />
    </div>
  );
}
