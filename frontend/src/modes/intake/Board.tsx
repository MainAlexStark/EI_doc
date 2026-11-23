/** Доска приёмок по стадиям: приём → поверка → готов к выдаче → выдан.
 *
 * Стадия считается из поверок (stageOf), поэтому «перевод» — это действие: внести приборы,
 * отметить «Выдано» (с «Отменить» в тосте), вернуть в работу. Руководство назначает
 * ответственного прямо в карточке. Приёмки, приехавшие с выезда-забора, помечены. */

import { useMemo, useState, type CSSProperties } from "react";
import { downloadOrderAct, issueOrder, type Employee, type WorkOrder } from "../../api";
import { assignWork, teamColors } from "../../dispatchApi";
import { errorText, ruShort } from "../../components/tripUtil";
import { EmptyState } from "../../ui/EmptyState";
import Icon from "../../ui/Icon";
import { confirmDialog } from "../../ui/Modal";
import toast from "../../ui/Toast";
import { flashOk } from "../../ui/transition";
import { Segmented } from "../../ui/controls";
import { PHONE, useMediaQuery } from "../../ui/hooks";
import { STAGES, stageOf, type IntakeOrder, type Stage } from "./api";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "") : "");

export default function Board({
  orders, employees, canAssign, onOpen, onChanged, onDelivery, onNew,
}: {
  orders: IntakeOrder[];
  employees: Employee[];
  canAssign: boolean;
  onOpen: (id: number) => void;
  /** Свежая приёмка — заменить в списке; null — перечитать всё. */
  onChanged: (order: WorkOrder | null) => void;
  onDelivery: (order: IntakeOrder) => void;
  onNew: () => void;
}) {
  const phone = useMediaQuery(PHONE);
  const [phoneStage, setPhoneStage] = useState<Stage>("receive");
  const [busy, setBusy] = useState<number | null>(null);
  const colors = useMemo(() => teamColors(employees.map((e) => e.id)), [employees]);

  const byStage = useMemo(() => {
    const map: Record<Stage, IntakeOrder[]> = { receive: [], verify: [], ready: [], issued: [] };
    for (const o of orders) map[stageOf(o)].push(o);
    for (const key of Object.keys(map) as Stage[]) {
      map[key].sort((a, b) =>
        key === "issued"
          ? (b.issued_at ?? "").localeCompare(a.issued_at ?? "")
          : (a.received_at ?? a.created_at).localeCompare(b.received_at ?? b.created_at),
      );
    }
    return map;
  }, [orders]);

  const run = async (o: IntakeOrder, job: () => Promise<void>) => {
    setBusy(o.id);
    try {
      await job();
    } catch (exc) {
      toast.error("Не получилось", { description: errorText(exc, "Нет связи — повторите") });
    } finally {
      setBusy(null);
    }
  };

  const issue = (o: IntakeOrder) =>
    run(o, async () => {
      if (o.verifications_ready < o.verifications_total) {
        const ok = await confirmDialog({ title: "Выдать приёмку?", text: `Поверено ${o.verifications_ready} из ${o.verifications_total}. Выдать всё равно?`, ok: "Выдать" });
        if (!ok) return;
      }
      const fresh = await issueOrder(o.id, true);
      onChanged(fresh);
      toast.success(`${o.client}: выдано`, {
        undo: async () => onChanged(await issueOrder(o.id, false)),
        action: { label: "Акт выдачи", onClick: () => void downloadOrderAct(o.id, "issue").catch(() => toast.error("Акт не сформировался")) },
      });
    });

  const unissue = (o: IntakeOrder) =>
    run(o, async () => {
      onChanged(await issueOrder(o.id, false));
      toast.info(`${o.client}: снова в работе`, { undo: async () => onChanged(await issueOrder(o.id, true)) });
    });

  const act = (o: IntakeOrder, kind: "receive" | "issue") =>
    run(o, () => downloadOrderAct(o.id, kind));

  const assign = (o: IntakeOrder, employeeId: number, target: HTMLElement | null) =>
    run(o, async () => {
      const before = o.assigned_employee_id;
      const result = await assignWork({ items: [{ type: "order", id: o.id }], employee_id: employeeId });
      if (result.errors.length) throw new Error(result.errors.map((e) => e.error).join("; "));
      const name = employees.find((e) => e.id === employeeId)?.full_name ?? "";
      onChanged({ ...o, assigned_employee_id: employeeId, assigned_employee: name });
      flashOk(target);
      toast.info(`${o.client} → ${name}`, {
        undo: async () => {
          await assignWork({ items: [{ type: "order", id: o.id }], employee_id: before });
          onChanged(null);
        },
      });
    });

  const card = (o: IntakeOrder) => {
    const stage = stageOf(o);
    const share = o.verifications_total ? (o.verifications_ready / o.verifications_total) * 100 : 0;
    return (
      <article key={o.id} className={`it-card ${busy === o.id ? "is-busy" : ""}`} data-order={o.id}>
        <button type="button" className="it-card-open" onClick={() => onOpen(o.id)}>
          <div className="it-card-top">
            <span className="data it-card-no">№ {o.id}</span>
            <span className="sub">{when(o.received_at ?? o.created_at)}</span>
          </div>
          <b className="it-card-client">{o.client}</b>
          <div className="sub">
            {[o.client_inn && `ИНН ${o.client_inn}`, o.contact, o.procurement_label && `закупка ${o.procurement_label.split(" · ")[0]}`].filter(Boolean).join(" · ")}
          </div>
          <div className="it-card-tags">
            {o.pickup && <span className="pill info plain" title={o.pickup.trip_title}><Icon name="truck" size={12} /> с выезда {ruShort(o.pickup.planned_date)}</span>}
            {o.delivery && <span className={`pill ${o.delivery.status === "done" ? "ok" : "warn"} plain`} title={o.delivery.trip_title}><Icon name="route" size={12} /> доставка {ruShort(o.delivery.planned_date)}</span>}
            {o.verifications_total === 0 && <span className="pill muted plain">приборы не внесены</span>}
          </div>
          {o.verifications_total > 0 && (
            <div className="it-card-progress">
              <span className="data">{o.verifications_ready}/{o.verifications_total}</span>
              <div className="mini-bar"><i style={{ width: `${share}%` }} /></div>
            </div>
          )}
        </button>
        <div className="it-card-foot">
          {canAssign ? (
            <div className="emp-select" style={{ "--vc": colors.get(o.assigned_employee_id) } as CSSProperties}>
              <span className="v-dot" />
              <select value={o.assigned_employee_id} disabled={busy === o.id} aria-label="Ответственный"
                      onChange={(e) => void assign(o, Number(e.target.value), e.currentTarget.closest(".it-card"))}>
                {!employees.some((e) => e.id === o.assigned_employee_id) && <option value={o.assigned_employee_id}>{o.assigned_employee}</option>}
                {employees.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
              </select>
            </div>
          ) : (
            <span className="sub">{o.assigned_employee}</span>
          )}
          <span className="spacer" />
          {stage === "receive" && (
            <button type="button" className="sm" onClick={() => onOpen(o.id)}><Icon name="plus" size={13} /> Приборы</button>
          )}
          {stage === "verify" && o.verifications_total > 0 && (
            <button type="button" className="sm" disabled={busy === o.id} onClick={() => void act(o, "receive")} title="Акт приёма"><Icon name="printer" size={13} /> Акт</button>
          )}
          {stage === "ready" && (
            <>
              {!o.delivery && (
                <button type="button" className="sm" onClick={() => onDelivery(o)} title="Вернуть с доставкой"><Icon name="truck" size={13} /></button>
              )}
              <button type="button" className="sm primary" disabled={busy === o.id} onClick={() => void issue(o)}><Icon name="check" size={13} /> Выдано</button>
            </>
          )}
          {stage === "issued" && (
            <>
              <button type="button" className="sm" disabled={busy === o.id} onClick={() => void act(o, "issue")} title="Акт выдачи"><Icon name="printer" size={13} /> Акт</button>
              <button type="button" className="sm ghost" disabled={busy === o.id} onClick={() => void unissue(o)} title="Вернуть в работу"><Icon name="undo" size={13} /></button>
            </>
          )}
        </div>
      </article>
    );
  };

  if (orders.length === 0) {
    return (
      <EmptyState icon="box" title="Приёмок нет" action={{ label: "Новая приёмка", icon: "plus", onClick: onNew, hotkey: "N" }}>
        Принесли коробку приборов — заведите приёмку: заказчик по ИНН, приборы таблицей со сканера, акт приёма.
      </EmptyState>
    );
  }

  if (phone) {
    return (
      <div className="it-board-phone">
        <Segmented
          value={phoneStage}
          onChange={setPhoneStage}
          size="sm"
          options={STAGES.map((s) => ({ value: s.key, label: s.key === "ready" ? "К выдаче" : s.title, count: byStage[s.key].length }))}
        />
        <div className="it-col-body">
          {byStage[phoneStage].length ? byStage[phoneStage].map(card) : <p className="sub it-col-empty">Пусто</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="it-board">
      {STAGES.map((s) => (
        <section key={s.key} className={`it-col is-${s.key}`}>
          <header className="it-col-head">
            <span className={`it-dot ${s.tone}`} /> {s.title} <span className="count">{byStage[s.key].length}</span>
          </header>
          <div className="it-col-body">
            {byStage[s.key].length ? byStage[s.key].map(card) : <p className="sub it-col-empty">Пусто</p>}
          </div>
        </section>
      ))}
    </div>
  );
}
