/** Режим 4 «Приёмки» — клиент сам принёс коробку (или её забрали с выезда).
 *
 * Доска по стадиям → «Новая приёмка» (заказчик → кто сдал → приборы таблицей → акт приёма) →
 * карточка приёмки: тот же экран ввода измерений и протоколов, что на выезде (FieldWork на
 * одном наряде), плюс «добавить таблицей» и «вернуть с доставкой».
 *
 * Адрес карточки — `#/intake/<id>` (id наряда-приёмки); так на неё ссылаются «Город» и агент.
 * Действия режима: `new`, `open-order` / `open` (id). */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchEmployees, type Employee, type WorkOrder } from "../../api";
import FieldWork from "../../components/FieldWork";
import { errorText, ruShort } from "../../components/tripUtil";
import { normalize, useCommands } from "../../ui/CommandPalette";
import { EmptyState } from "../../ui/EmptyState";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { Sheet } from "../../ui/Sheet";
import { SkeletonList } from "../../ui/Skeleton";
import toast from "../../ui/Toast";
import { PageActions, SearchField, Segmented } from "../../ui/controls";
import type { Go, ModeProps } from "../types";
import { useHashId } from "../procurements/hash";
import { useModeAction } from "../../ui/modeActions";
import { bulkInstruments, fetchIntake, fetchIntakeOne, stageOf, type BulkRow, type IntakeOrder } from "./api";
import Board from "./Board";
import DeliverySheet from "./DeliverySheet";
import FastTable from "./FastTable";
import NewIntake, { type Customer } from "./NewIntake";
import "./intake.css";

const POLL_MS = 60_000;

export default function IntakeMode({ me, isManager, go }: ModeProps) {
  const [openId, setOpenId] = useHashId("intake");
  const [active, setActive] = useState<IntakeOrder[] | null>(null);
  const [issued, setIssued] = useState<IntakeOrder[]>([]);
  const [error, setError] = useState("");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [query, setQuery] = useState("");
  const [who, setWho] = useState<"all" | "mine">("all");
  const [creating, setCreating] = useState(false);
  const [deliveryFor, setDeliveryFor] = useState<IntakeOrder | null>(null);

  const employeeId = me?.employee?.id ?? null;
  const ownOnly = !isManager;

  const load = useCallback(async () => {
    if (!me) return;
    if (ownOnly && !employeeId) return;
    try {
      const filter = ownOnly ? employeeId : null;
      const [open, done] = await Promise.all([
        fetchIntake({ issued: "0", employee: filter }),
        fetchIntake({ issued: "1", employee: filter, limit: 40 }),
      ]);
      setActive(open.filter((o) => o.status !== "cancelled"));
      setIssued(done.filter((o) => o.status !== "cancelled"));
      setError("");
    } catch (exc) {
      setError(errorText(exc, "Не удалось загрузить приёмки"));
    }
  }, [me, ownOnly, employeeId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (isManager) fetchEmployees().then(setEmployees).catch(() => undefined);
  }, [isManager]);

  useModeAction("intake", {
    new: () => setCreating(true),
    "open-order": (id) => typeof id === "number" && setOpenId(id),
    open: (id) => typeof id === "number" && setOpenId(id),
  });
  useCommands("intake-mode", [
    { id: "intake:new", title: "Новая приёмка", icon: "plus", group: "Приёмки", keywords: "приём коробка принести", hint: "n", run: () => setCreating(true) },
    { id: "intake:board", title: "Доска приёмок", icon: "board", group: "Приёмки", run: () => setOpenId(null) },
  ], []);

  const replace = useCallback((fresh: WorkOrder | null) => {
    if (!fresh) return void load();
    const merge = (list: IntakeOrder[]) => list.map((o) => (o.id === fresh.id ? { ...o, ...fresh } : o));
    if (fresh.issued_at) {
      setActive((list) => list?.filter((o) => o.id !== fresh.id) ?? list);
      setIssued((list) => (list.some((o) => o.id === fresh.id) ? merge(list) : [fresh as IntakeOrder, ...list]));
    } else {
      setIssued((list) => list.filter((o) => o.id !== fresh.id));
      setActive((list) => (list == null ? list : list.some((o) => o.id === fresh.id) ? merge(list) : [fresh as IntakeOrder, ...list]));
    }
  }, [load]);

  // Недавние заказчики — из последних приёмок, без повторов, физлиц не считаем.
  const recent: Customer[] = useMemo(() => {
    const all = [...(active ?? []), ...issued].sort((a, b) => (b.received_at ?? b.created_at).localeCompare(a.received_at ?? a.created_at));
    const seen = new Set<number>();
    const out: Customer[] = [];
    for (const o of all) {
      if (seen.has(o.client_id) || o.client === "Физическое лицо" || o.client_kind === "person") continue;
      seen.add(o.client_id);
      out.push({ client_id: o.client_id, inn: o.client_inn, name: o.client, address: o.client_address, phone: o.client_phone });
    }
    return out;
  }, [active, issued]);

  const shown = useMemo(() => {
    const q = normalize(query);
    return [...(active ?? []), ...issued]
      .filter((o) => who === "all" || o.assigned_employee_id === employeeId)
      .filter((o) => !q || normalize(`${o.id} ${o.client} ${o.client_inn} ${o.contact} ${o.procurement_label} ${o.assigned_employee}`).includes(q));
  }, [active, issued, query, who, employeeId]);

  if (me && !isManager && !me.employee) {
    return (
      <EmptyState icon="lock" title="Нет привязки к сотруднику">
        Приёмки доступны сотрудникам с привязанной учётной записью — попросите руководителя связать её с вашей карточкой.
      </EmptyState>
    );
  }

  if (openId != null) {
    return <OrderView key={openId} id={openId} me={me} isManager={isManager} employees={employees} go={go}
                      onBack={() => { setOpenId(null); void load(); }} />;
  }

  const counts = { ready: (active ?? []).filter((o) => stageOf(o) === "ready").length };

  return (
    <div className="it-mode">
      <PageActions>
        <button type="button" className="primary" onClick={() => setCreating(true)}>
          <Icon name="plus" size={16} /> Приёмка <Kbd className="kbd-hint">N</Kbd>
        </button>
      </PageActions>
      <div className="toolbar it-toolbar">
        <SearchField value={query} onChange={setQuery} placeholder="Заказчик, ИНН, № приёмки" className="grow" />
        {isManager && employeeId && (
          <Segmented value={who} onChange={setWho} size="sm" options={[{ value: "all", label: "Все" }, { value: "mine", label: "Мои" }]} />
        )}
        {counts.ready > 0 && <span className="pill ok plain">к выдаче: {counts.ready}</span>}
      </div>
      {error && <EmptyState tone="error" title="Приёмки не загрузились" action={{ label: "Повторить", onClick: () => void load() }} compact>{error}</EmptyState>}
      {!error && active == null && <SkeletonList rows={4} />}
      {active != null && (
        <Board
          orders={shown}
          employees={employees}
          canAssign={isManager}
          onOpen={setOpenId}
          onChanged={replace}
          onDelivery={setDeliveryFor}
          onNew={() => setCreating(true)}
        />
      )}

      {creating && (
        <NewIntake
          me={me}
          isManager={isManager}
          recent={recent}
          onClose={() => { setCreating(false); void load(); }}
          onCreated={replace}
          onOpen={(id) => { setCreating(false); setOpenId(id); }}
        />
      )}
      {deliveryFor && (
        <DeliverySheet order={deliveryFor} me={me} isManager={isManager} employees={employees} go={go}
                       onClose={() => setDeliveryFor(null)} onDone={() => { setDeliveryFor(null); void load(); }} />
      )}
    </div>
  );
}

/** Одна приёмка: отметки выезда, «добавить таблицей», доставка — и привычный экран ввода. */
function OrderView({
  id, me, isManager, employees, go, onBack,
}: {
  id: number;
  me: ModeProps["me"];
  isManager: boolean;
  employees: Employee[];
  go: Go;
  onBack: () => void;
}) {
  const [order, setOrder] = useState<IntakeOrder | null>(null);
  const [version, setVersion] = useState(0);
  const [table, setTable] = useState(false);
  const [delivery, setDelivery] = useState(false);

  const reload = useCallback(() => {
    fetchIntakeOne(id).then(setOrder).catch(() => undefined);
  }, [id]);
  useEffect(() => {
    reload();
  }, [reload, version]);

  useHotkey("t", () => setTable(true), { enabled: order?.kind === "lab" && !order.issued_at, description: "Добавить приборы таблицей", group: "Приёмки" });

  const save = async (rows: BulkRow[]) => {
    try {
      const result = await bulkInstruments(id, rows);
      toast.success(`Добавлено приборов: ${result.created.length}`, result.errors.length ? { description: `С ошибкой: ${result.errors.length}` } : undefined);
      setVersion((v) => v + 1);
      return result;
    } catch (exc) {
      toast.error("Не сохранилось", { description: errorText(exc, "Повторите — строки не потеряются") });
      return null;
    }
  };

  const isLab = order?.kind === "lab";
  return (
    <div className="it-order">
      {isLab && (
        <PageActions>
          {!order.issued_at && (
            <button type="button" onClick={() => setTable(true)}><Icon name="keyboard" size={15} /> <span className="hide-phone">Таблицей</span> <Kbd className="kbd-hint">T</Kbd></button>
          )}
          {!order.issued_at && !order.delivery && order.verifications_total > 0 && (
            <button type="button" onClick={() => setDelivery(true)}><Icon name="truck" size={15} /> <span className="hide-phone">С доставкой</span></button>
          )}
        </PageActions>
      )}
      {isLab && (order.pickup || order.delivery) && (
        <div className="it-marks">
          {order.pickup && (
            <button type="button" className="pill info plain" onClick={() => go("city", { action: "open-trip", payload: order.pickup!.trip_id })}>
              <Icon name="truck" size={13} /> Забрали с выезда {ruShort(order.pickup.planned_date)} · {order.pickup.trip_title}
            </button>
          )}
          {order.delivery && (
            <button type="button" className={`pill ${order.delivery.status === "done" ? "ok" : "warn"} plain`} onClick={() => go("city", { action: "open-trip", payload: order.delivery!.trip_id })}>
              <Icon name="route" size={13} /> Доставка {ruShort(order.delivery.planned_date)} · {order.delivery.trip_title}
            </button>
          )}
        </div>
      )}
      <FieldWork key={version} me={me} isManager={isManager} openOrderId={id} backLabel="Приёмки" fallbackTitle={`Приёмка № ${id}`} onClose={onBack} />

      <Sheet open={table} onClose={() => setTable(false)} size="lg" title="Приборы таблицей" subtitle={order ? `${order.client} · № ${order.id}` : undefined}>
        {table && <FastTable onSave={save} saveLabel="Добавить" />}
      </Sheet>
      {delivery && order && (
        <DeliverySheet order={order} me={me} isManager={isManager} employees={employees} go={go}
                       onClose={() => setDelivery(false)} onDone={() => { setDelivery(false); reload(); }} />
      )}
    </div>
  );
}
