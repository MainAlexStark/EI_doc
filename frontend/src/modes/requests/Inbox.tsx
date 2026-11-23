/** Режим 6 «Заявки» — разбор входящих потоком (docs/rebuild.md, «Заявки»).
 *
 * Список слева, карточка справа; на телефоне — список, карточка листом снизу.
 * J/K (↓/↑) — следующая/предыдущая, Shift — расширить выделение, Enter — открыть,
 * A — назначить, C — подтвердить, X — отклонить, S — спам, / — поиск, Esc — снять выделение.
 * После действия — сразу следующая заявка и тост; отклонение и спам уходят на сервер
 * через несколько секунд, поэтому «Отменить» в тосте честно отменяет; уже отправленное
 * «Отменить» возвращает во «Новые» (bulk action=reopen). */

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { fetchEmployees, fetchRequests, type Employee, type HubRequest } from "../../api";
import type { TeamCard } from "../../dispatchApi";
import AssignPicker, { type PickPerson } from "../../components/control/AssignPicker";
import { useCommands } from "../../ui/CommandPalette";
import { EmptyState } from "../../ui/EmptyState";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { promptDialog } from "../../ui/Modal";
import { Sheet } from "../../ui/Sheet";
import { SkeletonList } from "../../ui/Skeleton";
import toast from "../../ui/Toast";
import { Segmented } from "../../ui/controls";
import { PHONE, useMediaQuery } from "../../ui/hooks";
import { useModeAction } from "../../ui/modeActions";
import type { Go } from "../types";
import { bulkRequests, fetchLoad, type BulkResult } from "./api";
import RequestCard, { CardActions, type CardAction } from "./Card";
import { BOX_STATUSES, SOURCE, STATUS, itemsLine, money, nRequests, receivedAt, whenLine, type Box } from "./util";
import "./requests.css";

const POLL_MS = 30_000;
const DEFER_MS = 6_500; // отклонение/спам ждут столько, сколько висит тост с «Отменить»

type Picker = { mode: "assign" | "confirm"; ids: number[] };
type Deferred = { ids: number[]; action: "reject" | "spam"; reason: string; timer: number; prev: Map<number, string> };

const errorText = (exc: unknown, fallback: string) => (exc instanceof Error && exc.message ? exc.message : fallback);
const byNewest = (a: HubRequest, b: HubRequest) => b.created_at.localeCompare(a.created_at);

export default function Inbox({ onChanged, go, onNew }: { onChanged?: () => void; go?: Go; onNew?: () => void }) {
  const phone = useMediaQuery(PHONE);
  const [requests, setRequests] = useState<HubRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [box, setBox] = useState<Box>("new");
  const [source, setSource] = useState("");
  const [district, setDistrict] = useState("");
  const [query, setQuery] = useState("");
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const [sheetOpen, setSheetOpen] = useState(false); // телефон: карточка листом
  const [picker, setPicker] = useState<Picker | null>(null);
  const [busy, setBusy] = useState(false);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [load, setLoad] = useState<Map<number, TeamCard>>(new Map());
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const known = useRef<Set<number> | null>(null);
  const deferred = useRef<Map<string, Deferred>>(new Map());
  const anchor = useRef<number | null>(null);

  // ------------------------------------------------------------ загрузка
  const reload = useCallback(async (quiet = false) => {
    try {
      const rows = await fetchRequests({});
      // заявки, чьё отклонение ещё ждёт отправки, показываем в новом статусе
      const waiting = new Map<number, string>();
      deferred.current.forEach((d) => d.ids.forEach((id) => waiting.set(id, d.action === "spam" ? "spam" : "rejected")));
      const merged = rows.map((r) => (waiting.has(r.id) ? { ...r, status: waiting.get(r.id)! } : r)).sort(byNewest);
      if (known.current) {
        const arrived = merged.filter((r) => !known.current!.has(r.id) && r.status === "new");
        if (arrived.length) {
          setFresh((prev) => new Set([...prev, ...arrived.map((r) => r.id)]));
          toast.info(arrived.length === 1 ? `Новая заявка №${arrived[0].id}` : `Новых заявок: ${arrived.length}`, {
            description: arrived.length === 1 ? [arrived[0].contact_name, arrived[0].address].filter(Boolean).join(" · ") : undefined,
            action: { label: "Показать", onClick: () => { setBox("new"); setCurrentId(arrived[0].id); } },
          });
          onChanged?.();
        }
      }
      known.current = new Set(merged.map((r) => r.id));
      setRequests(merged);
      setError("");
    } catch (exc) {
      if (!quiet) setError(errorText(exc, "Не удалось загрузить заявки"));
    } finally {
      setLoading(false);
    }
  }, [onChanged]);

  useEffect(() => {
    void reload();
    fetchEmployees().then(setEmployees).catch(() => undefined);
  }, [reload]);

  // Новые заявки — опросом, только пока вкладка на экране.
  useEffect(() => {
    let timer = 0;
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        if (document.visibilityState === "visible") await reload(true);
        schedule();
      }, POLL_MS);
    };
    const onVisible = () => { if (document.visibilityState === "visible") void reload(true); };
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [reload]);

  // Отложенные отклонения не теряем: уход со страницы или из режима — отправляем сразу.
  useEffect(() => {
    const flush = () => {
      deferred.current.forEach((d) => {
        window.clearTimeout(d.timer);
        void bulkRequests(d.action, d.ids, { reason: d.reason }, { keepalive: true }).catch(() => undefined);
      });
      deferred.current.clear();
    };
    window.addEventListener("pagehide", flush);
    return () => { window.removeEventListener("pagehide", flush); flush(); };
  }, []);

  // ------------------------------------------------------------ список
  const districts = useMemo(
    () => [...new Set(requests.map((r) => r.district).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")),
    [requests],
  );
  const sources = useMemo(() => [...new Set(requests.map((r) => r.source))], [requests]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^№\s*/, "");
    const qDigits = q.replace(/\D/g, "");
    return requests.filter((r) => {
      if (!BOX_STATUSES[box].includes(r.status)) return false;
      if (source && r.source !== source) return false;
      if (district && r.district !== district) return false;
      if (!q) return true;
      if (String(r.id) === q) return true;
      if (qDigits.length >= 4 && r.contact_phone.replace(/\D/g, "").includes(qDigits)) return true;
      return `${r.contact_name} ${r.address} ${r.district} ${r.comment} ${r.contact_email} ${itemsLine(r)}`.toLowerCase().includes(q);
    });
  }, [requests, box, source, district, query]);

  const counts = useMemo(() => ({
    new: requests.filter((r) => BOX_STATUSES.new.includes(r.status)).length,
    work: requests.filter((r) => r.status === "confirmed").length,
  }), [requests]);

  // На широком экране всегда открыта какая-то заявка — как в «Почте».
  useEffect(() => {
    if (loading) return;
    if (currentId !== null && shown.some((r) => r.id === currentId)) return;
    if (currentId !== null && requests.some((r) => r.id === currentId) && sheetOpen) return; // открыта из палитры
    setCurrentId(phone ? null : shown[0]?.id ?? null);
  }, [shown, loading, currentId, phone, requests, sheetOpen]);

  const current = requests.find((r) => r.id === currentId) ?? null;

  useEffect(() => {
    if (currentId === null) return;
    setFresh((prev) => (prev.has(currentId) ? new Set([...prev].filter((id) => id !== currentId)) : prev));
    listRef.current?.querySelector(`[data-id="${currentId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [currentId]);

  /** Над чем действие: выделенные, иначе текущая. */
  const targets = useCallback((): HubRequest[] => {
    const ids = selected.size > 0 ? selected : currentId !== null ? new Set([currentId]) : new Set<number>();
    return requests.filter((r) => ids.has(r.id));
  }, [selected, currentId, requests]);

  /** Следующая заявка после ухода ids из списка. */
  const nextAfter = useCallback((ids: number[]) => {
    const gone = new Set(ids);
    const index = shown.findIndex((r) => r.id === currentId);
    const after = shown.slice(index + 1).find((r) => !gone.has(r.id));
    const before = [...shown.slice(0, Math.max(0, index))].reverse().find((r) => !gone.has(r.id));
    return (after ?? before)?.id ?? null;
  }, [shown, currentId]);

  const move = (delta: number, extend = false) => {
    if (!shown.length) return;
    const index = shown.findIndex((r) => r.id === currentId);
    const nextIndex = index < 0 ? 0 : Math.max(0, Math.min(shown.length - 1, index + delta));
    const next = shown[nextIndex].id;
    if (extend) {
      setSelected((prev) => {
        const set = new Set(prev);
        if (currentId !== null) set.add(currentId);
        set.add(next);
        return set;
      });
    }
    setCurrentId(next);
    anchor.current = extend ? anchor.current ?? currentId : next;
  };

  const clickRow = (event: MouseEvent, id: number) => {
    if (event.shiftKey && (anchor.current ?? currentId) !== null) {
      const from = shown.findIndex((r) => r.id === (anchor.current ?? currentId));
      const to = shown.findIndex((r) => r.id === id);
      const [a, b] = from < to ? [from, to] : [to, from];
      setSelected(new Set(shown.slice(a, b + 1).map((r) => r.id)));
      setCurrentId(id);
      return;
    }
    if (event.metaKey || event.ctrlKey) {
      setSelected((prev) => {
        const set = new Set(prev);
        if (set.size === 0 && currentId !== null && currentId !== id) set.add(currentId);
        if (set.has(id)) set.delete(id); else set.add(id);
        return set;
      });
      anchor.current = id;
      setCurrentId(id);
      return;
    }
    setSelected(new Set());
    anchor.current = id;
    setCurrentId(id);
    if (phone) setSheetOpen(true);
  };

  const toggleCheck = (id: number) =>
    setSelected((prev) => {
      const set = new Set(prev);
      if (set.has(id)) set.delete(id); else set.add(id);
      return set;
    });

  // ------------------------------------------------------------ действия
  const applyResult = (result: BulkResult) => {
    const byId = new Map(result.items.map((r) => [r.id, r]));
    setRequests((prev) => prev.map((r) => byId.get(r.id) ?? r));
  };

  const reportProblems = (result: BulkResult) => {
    const problems = [
      ...result.errors.map((e) => `№${e.id}: ${e.error}`),
      ...result.skipped.map((s) => `№${s.id}: ${s.reason}`),
    ];
    if (problems.length) toast.warn(`Не со всеми получилось: ${problems.length}`, { description: problems.slice(0, 3).join("; ") });
  };

  const finish = (ids: number[], leavesBox: boolean) => {
    if (leavesBox) setCurrentId(nextAfter(ids));
    setSelected(new Set());
    if (phone) setSheetOpen(false);
    onChanged?.();
  };

  const nameOf = (id: number | null | undefined) => employees.find((e) => e.id === id)?.full_name ?? "исполнителю";

  const doAssign = async (list: HubRequest[], employeeId: number) => {
    const ids = list.map((r) => r.id);
    const prev = new Map(list.map((r) => [r.id, r.suggested_employee_id]));
    setBusy(true);
    try {
      const result = await bulkRequests("assign", ids, { employee_id: employeeId });
      applyResult(result);
      reportProblems(result);
      if (result.done.length) {
        const who = nameOf(employeeId);
        // У кого кто-то был подобран раньше — вернуть его; остальных — во «Новые» (reopen).
        const restorable = result.done.filter((id) => prev.get(id));
        const fresh = result.done.filter((id) => !prev.get(id));
        toast.success(result.done.length === 1 ? `Заявка №${result.done[0]} → ${who}` : `${nRequests(result.done.length)} → ${who}`, {
          undo: async () => {
            const groups = new Map<number, number[]>();
            restorable.forEach((id) => groups.set(prev.get(id)!, [...(groups.get(prev.get(id)!) ?? []), id]));
            for (const [emp, group] of groups) applyResult(await bulkRequests("assign", group, { employee_id: emp }));
            if (fresh.length) applyResult(await bulkRequests("reopen", fresh));
            toast.info("Назначение отменено");
            onChanged?.();
          },
        });
      }
      finish(ids, false);
      // назначили — дальше по потоку
      if (list.length === 1) setCurrentId(nextAfter([]) ?? currentId);
    } catch (exc) {
      toast.error("Не удалось назначить", { description: errorText(exc, "Повторите позже") });
    } finally {
      setBusy(false);
    }
  };

  const doConfirm = async (list: HubRequest[], employeeId?: number, date?: string | null, time?: string | null) => {
    const ids = list.map((r) => r.id);
    setBusy(true);
    const tid = toast.info(ids.length > 1 ? `Подтверждаю ${nRequests(ids.length)}…` : `Подтверждаю №${ids[0]}…`, { duration: 0 });
    try {
      const result = await bulkRequests("confirm", ids, {
        employee_id: employeeId ?? null,
        ...(date ? { scheduled_date: date } : {}),
        ...(time ? { scheduled_time: time } : {}),
      });
      applyResult(result);
      toast.dismiss(tid);
      reportProblems(result);
      if (result.done.length) {
        const one = result.items.find((r) => r.id === result.done[0]);
        toast.success(
          result.done.length === 1 ? `Заявка №${result.done[0]} подтверждена` : `Подтверждено: ${nRequests(result.done.length)}`,
          {
            description: result.done.length === 1 ? `Наряд создан — выезд у ${one?.assigned_employee || "исполнителя"} в «Городе» и графике` : "Наряды созданы — выезды у исполнителей",
            action: go ? { label: "К выездам", onClick: () => go("city") } : undefined,
            undo: () => void reopen(result.done, "Подтверждение отменено — наряд снят"),
          },
        );
      }
      finish(result.done, true);
    } catch (exc) {
      toast.error("Не удалось подтвердить", { id: tid, description: errorText(exc, "Повторите позже") });
    } finally {
      setBusy(false);
    }
  };

  /** «Отменить» после фактической отправки: вернуть во «Новые» на сервере. */
  const reopen = async (ids: number[], done: string) => {
    try {
      const result = await bulkRequests("reopen", ids);
      applyResult(result);
      reportProblems(result);
      if (result.done.length) {
        toast.info(done);
        setCurrentId(result.done[0]);
      }
      onChanged?.();
    } catch (exc) {
      toast.error("Не удалось вернуть", { description: errorText(exc, "Повторите позже") });
    }
  };

  const doReject = (list: HubRequest[], action: "reject" | "spam", reason = "") => {
    const ids = list.map((r) => r.id);
    const prev = new Map(list.map((r) => [r.id, r.status]));
    const key = `d${Date.now()}`;
    const next = nextAfter(ids);
    setRequests((rows) => rows.map((r) => (prev.has(r.id) ? { ...r, status: action === "spam" ? "spam" : "rejected", reject_reason: reason || r.reject_reason } : r)));
    const commit = async () => {
      deferred.current.delete(key);
      try {
        const result = await bulkRequests(action, ids, { reason });
        applyResult(result);
        reportProblems(result);
        onChanged?.();
      } catch (exc) {
        setRequests((rows) => rows.map((r) => (prev.has(r.id) ? { ...r, status: prev.get(r.id)! } : r)));
        toast.error(action === "spam" ? "Не удалось отправить в спам" : "Не удалось отклонить", { description: errorText(exc, "Заявки вернулись во входящие") });
      }
    };
    deferred.current.set(key, { ids, action, reason, prev, timer: window.setTimeout(() => void commit(), DEFER_MS) });
    const label = ids.length === 1 ? `Заявка №${ids[0]}` : nRequests(ids.length);
    toast.info(action === "spam" ? `${label} — в спам` : `${label} — отклонена`, {
      description: reason || undefined,
      duration: DEFER_MS - 300,
      undo: () => {
        const d = deferred.current.get(key);
        if (!d) return void reopen(ids, "Заявки вернулись во «Новые»"); // уже отправлено — вернуть на сервере
        window.clearTimeout(d.timer);
        deferred.current.delete(key);
        setRequests((rows) => rows.map((r) => (prev.has(r.id) ? { ...r, status: prev.get(r.id)! } : r)));
        setCurrentId(ids[0]);
      },
    });
    setCurrentId(next);
    setSelected(new Set());
    if (phone) setSheetOpen(false);
  };

  const act = async (action: CardAction) => {
    if (busy) return;
    const list = targets().filter((r) => r.status === "new" || r.status === "routed");
    if (!list.length) {
      toast.info("Эта заявка уже разобрана", { description: "Действия — только для новых заявок" });
      return;
    }
    if (action === "assign") return setPicker({ mode: "assign", ids: list.map((r) => r.id) });
    if (action === "confirm") {
      if (list.every((r) => r.suggested_employee_id)) return void doConfirm(list);
      return setPicker({ mode: "confirm", ids: list.map((r) => r.id) });
    }
    if (action === "spam") return doReject(list, "spam");
    const reason = await promptDialog({
      title: list.length === 1 ? `Отклонить заявку №${list[0].id}` : `Отклонить ${nRequests(list.length)}`,
      text: "Причину увидят коллеги в архиве.",
      ok: "Отклонить",
      placeholder: "Например, не наш район",
    });
    if (reason === null) return;
    doReject(list, "reject", reason.trim());
  };

  // ------------------------------------------------------------ пикер
  const pickerList = useMemo(() => (picker ? requests.filter((r) => picker.ids.includes(r.id)) : []), [picker, requests]);
  const pickerDate = pickerList.find((r) => r.desired_date)?.desired_date ?? null;
  useEffect(() => {
    if (!picker) return;
    void fetchLoad(pickerDate ?? undefined).then(setLoad);
  }, [picker, pickerDate]);

  const people: PickPerson[] = useMemo(() => {
    const suggested = new Set(pickerList.map((r) => r.suggested_employee_id).filter(Boolean) as number[]);
    const busyness = (id: number) => load.get(id)?.stops.total ?? 0;
    return [...employees]
      .sort((a, b) => Number(suggested.has(b.id)) - Number(suggested.has(a.id)) || busyness(a.id) - busyness(b.id) || a.full_name.localeCompare(b.full_name, "ru"))
      .map((e) => {
        const card = load.get(e.id);
        const bits: string[] = [];
        if (suggested.has(e.id)) bits.push("по району");
        if (card) {
          if (card.state === "off") bits.push("не работает");
          else if (card.state === "trip") bits.push("в командировке");
          bits.push(`${card.stops.total} адр.`);
          if (card.week_stops) bits.push(`нед. ${card.week_stops}`);
        }
        return {
          id: e.id,
          name: e.full_name,
          hint: bits.join(" · ") || undefined,
          muted: card?.state === "off" || card?.state === "trip",
          color: suggested.has(e.id) ? "var(--accent)" : undefined,
        };
      });
  }, [employees, load, pickerList]);

  // ------------------------------------------------------------ клавиши, палитра, действия режима
  const G = "Заявки";
  useHotkey(["j", "arrowdown"], () => move(1), { description: "Следующая заявка", group: G });
  useHotkey(["k", "arrowup"], () => move(-1), { description: "Предыдущая заявка", group: G });
  useHotkey(["shift+j", "shift+arrowdown"], () => move(1, true), { description: "Выделить и вниз", group: G });
  useHotkey(["shift+k", "shift+arrowup"], () => move(-1, true), { description: "Выделить и вверх", group: G });
  useHotkey("a", () => void act("assign"), { description: "Назначить исполнителя", group: G });
  useHotkey("c", () => void act("confirm"), { description: "Подтвердить — создать наряд", group: G });
  useHotkey("x", () => void act("reject"), { description: "Отклонить с причиной", group: G });
  useHotkey("s", () => void act("spam"), { description: "В спам", group: G });
  useHotkey("esc", () => setSelected(new Set()), { enabled: selected.size > 0, description: "Снять выделение", group: G });
  useHotkey("/", () => searchRef.current?.focus(), { description: "Поиск по заявкам", group: G });

  // «/» в режиме — свой поиск, а не общая палитра: перехватываем раньше неё.
  // Enter — открыть заявку, но не мешаем Enter на кнопках и ссылках.
  const enterRef = useRef(() => {});
  enterRef.current = () => {
    if (phone) setSheetOpen(currentId !== null);
    else document.querySelector<HTMLElement>(".rq-detail")?.focus();
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      if (event.key !== "/" && event.key !== "Enter") return;
      const el = event.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (document.body.classList.contains("modal-open") || document.querySelector(".sheet-layer:not(.modeless):not(.closing), .palette-layer")) return;
      if (event.key === "Enter") {
        if (el && el.closest("button, a, [role='button']")) return;
        event.preventDefault();
        enterRef.current();
        return;
      }
      event.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  useModeAction("requests", {
    open: (id) => {
      if (typeof id !== "number") return;
      const found = requests.find((r) => r.id === id);
      if (found) setBox(BOX_STATUSES.new.includes(found.status) ? "new" : found.status === "confirmed" ? "work" : "archive");
      setQuery("");
      setCurrentId(id);
      if (phone) setSheetOpen(true);
    },
    search: (q) => { if (typeof q === "string") { setQuery(q); setBox("new"); } },
  }, !loading);

  useCommands("requests-mode", [
    { id: "requests:new", title: "Записать заявку по телефону", icon: "phone", group: G, keywords: "новая звонок запись", hint: "n", run: () => onNew?.() },
    ...(current && BOX_STATUSES.new.includes(current.status) ? [
      { id: "requests:confirm", title: `Подтвердить №${current.id}`, subtitle: current.contact_name, icon: "check" as const, group: G, hint: "c", run: () => void act("confirm") },
      { id: "requests:assign", title: `Назначить №${current.id}`, subtitle: current.contact_name, icon: "user" as const, group: G, hint: "a", run: () => void act("assign") },
    ] : []),
    { id: "requests:box-new", title: "Новые заявки", icon: "inbox", group: G, run: () => setBox("new") },
    { id: "requests:box-work", title: "Заявки в работе", icon: "clock", group: G, run: () => setBox("work") },
    { id: "requests:box-archive", title: "Архив заявок", icon: "folder", group: G, run: () => setBox("archive") },
  ], [current?.id, current?.status, onNew]);

  // ------------------------------------------------------------ разметка
  if (loading) return <div className="rq-loading"><SkeletonList rows={6} label="Загружаю заявки…" /></div>;

  const many = selected.size > 1;
  const selectedList = requests.filter((r) => selected.has(r.id));
  const filtered = Boolean(query || source || district);

  const detail = many ? (
    <div className="rq-batch">
      <div className="rq-batch-count data">{selected.size}</div>
      <h2>{nRequests(selected.size)} выделено</h2>
      <p className="muted">Действие применится ко всем новым из них — по очереди, с одним итогом.</p>
      <ul className="rq-batch-list">
        {selectedList.slice(0, 8).map((r) => (
          <li key={r.id}><span className="data">№{r.id}</span> {r.contact_name} <span className="muted">· {r.district || r.address}</span></li>
        ))}
        {selectedList.length > 8 && <li className="muted">и ещё {selectedList.length - 8}</li>}
      </ul>
      {selectedList.some((r) => BOX_STATUSES.new.includes(r.status)) && <CardActions request={selectedList.find((r) => BOX_STATUSES.new.includes(r.status))!} busy={busy} onAction={(a) => void act(a)} />}
      <button type="button" className="ghost" onClick={() => setSelected(new Set())}>Снять выделение <Kbd className="kbd-hint">Esc</Kbd></button>
    </div>
  ) : current ? (
    <RequestCard key={current.id} request={current} busy={busy} onAction={(a) => void act(a)} showActions={!phone} />
  ) : null;

  return (
    <div className="split rq">
      <div className="split-list">
        <div className="split-list-head">
          <div className="rq-toolbar">
            <Segmented
              size="sm"
              value={box}
              ariaLabel="Ящик"
              onChange={(next) => { setBox(next); setSelected(new Set()); setCurrentId(null); }}
              options={[
                { value: "new", label: "Новые", count: counts.new },
                { value: "work", label: "В работе", count: counts.work },
                { value: "archive", label: "Архив" },
              ]}
            />
            {onNew && (
              <button type="button" className="icon-btn" title="Записать по телефону — N" aria-label="Записать по телефону" onClick={onNew}>
                <Icon name="plus" size={18} />
              </button>
            )}
          </div>
          <label className="search-field rq-search">
            <Icon name="search" size={16} />
            <input
              ref={searchRef}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") { setQuery(""); (e.target as HTMLInputElement).blur(); }
                if (e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); (e.target as HTMLInputElement).blur(); if (!currentId && shown[0]) setCurrentId(shown[0].id); }
              }}
              placeholder="Имя, телефон, адрес, №"
              aria-label="Поиск по заявкам"
            />
            <Kbd className="kbd-hint">/</Kbd>
          </label>
          <div className="rq-filters">
            <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Источник">
              <option value="">Все источники</option>
              {sources.map((s) => <option key={s} value={s}>{SOURCE[s] ?? s}</option>)}
            </select>
            <select value={district} onChange={(e) => setDistrict(e.target.value)} aria-label="Район">
              <option value="">Все районы</option>
              {districts.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
        </div>

        <div className="split-list-body" ref={listRef}>
          {error && (
            <EmptyState tone="error" compact title="Заявки не загрузились" action={{ label: "Повторить", icon: "refresh", onClick: () => void reload() }}>
              {error}
            </EmptyState>
          )}
          {!error && shown.length === 0 && (
            filtered ? (
              <EmptyState icon="search" compact title="Ничего не нашлось" action={{ label: "Сбросить фильтры", onClick: () => { setQuery(""); setSource(""); setDistrict(""); } }}>
                Под запрос и фильтры нет заявок.
              </EmptyState>
            ) : box === "new" ? (
              <EmptyState icon="checkCircle" title="Всё разобрано" action={onNew ? { label: "Записать по телефону", icon: "phone", onClick: onNew, hotkey: "n" } : undefined}>
                Новые заявки с сайта появятся здесь сами — с подобранным по району исполнителем.
              </EmptyState>
            ) : (
              <EmptyState icon="inbox" compact title={box === "work" ? "В работе пусто" : "Архив пуст"} />
            )
          )}
          <div className="mail-list" role="listbox" aria-label="Заявки" aria-multiselectable="true">
            {shown.map((r) => {
              const isSel = selected.has(r.id);
              const when = whenLine(r);
              return (
                <div
                  key={r.id}
                  data-id={r.id}
                  role="option"
                  aria-selected={r.id === currentId || isSel}
                  tabIndex={-1}
                  className={`mail-item rq-item ${r.id === currentId ? "active" : ""} ${isSel ? "is-selected" : ""} ${fresh.has(r.id) ? "is-fresh" : ""}`}
                  onClick={(e) => clickRow(e, r.id)}
                >
                  {r.status === "new" && <span className="unread" aria-label="новая" />}
                  <input
                    type="checkbox"
                    className="rq-check"
                    checked={isSel}
                    aria-label={`Выделить заявку №${r.id}`}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleCheck(r.id)}
                  />
                  <span className="mail-top">
                    <b>{r.contact_name || "Без имени"}</b>
                    <span className="mail-date">{receivedAt(r.created_at)}</span>
                  </span>
                  <span className="sub">{itemsLine(r)}{r.estimated_price ? ` · ${money(r.estimated_price)} ₽` : ""}</span>
                  <span className="mail-meta">{[r.district, r.address].filter(Boolean).join(" · ")}</span>
                  <span className="mail-tags">
                    {box === "archive" && <span className={`pill ${STATUS[r.status]?.tone ?? "muted"}`}>{STATUS[r.status]?.label ?? r.status}</span>}
                    {r.source !== "site" && <span className="tag">{SOURCE[r.source] ?? r.source}</span>}
                    {when && <span className="tag"><Icon name="calendar" size={11} />{when}</span>}
                    {(r.assigned_employee || r.suggested_employee) && <span className="tag info"><Icon name="user" size={11} />{r.assigned_employee || r.suggested_employee}</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
        {shown.length > 0 && !phone && (
          <div className="rq-legend kbd-hint" aria-hidden="true">
            <Kbd>J</Kbd><Kbd>K</Kbd> листать · <Kbd>Enter</Kbd> открыть · <Kbd>C</Kbd> подтвердить · <Kbd>A</Kbd> назначить · <Kbd>X</Kbd> отклонить · <Kbd>?</Kbd> все клавиши
          </div>
        )}
      </div>

      {!phone && (
        <div className="split-detail rq-detail" tabIndex={-1}>
          <div className="split-detail-inner">
            {detail ?? (shown.length > 0 && <EmptyState icon="inbox" title="Заявка не выбрана">Выберите заявку слева или нажмите <Kbd>J</Kbd>.</EmptyState>)}
          </div>
        </div>
      )}

      {phone && (
        <Sheet
          open={sheetOpen && (current !== null || many)}
          onClose={() => setSheetOpen(false)}
          side="bottom"
          size="lg"
          title={many ? `Выделено: ${selected.size}` : current ? `Заявка №${current.id}` : ""}
          footer={!many && current ? <CardActions request={current} busy={busy} onAction={(a) => void act(a)} /> : undefined}
        >
          {detail}
        </Sheet>
      )}
      {phone && selected.size > 0 && !sheetOpen && (
        <div className="rq-phone-batch">
          <span>{nRequests(selected.size)}</span>
          <button type="button" className="primary" onClick={() => setSheetOpen(true)}>Действия</button>
          <button type="button" className="ghost" onClick={() => setSelected(new Set())}>Снять</button>
        </div>
      )}

      <Sheet
        open={picker !== null}
        onClose={() => setPicker(null)}
        size="sm"
        title={picker?.mode === "confirm" ? "Подтвердить — кому выезд" : "Назначить исполнителя"}
        subtitle={pickerList.length === 1
          ? `№${pickerList[0].id} · ${pickerList[0].district || pickerList[0].address}`
          : nRequests(pickerList.length)}
      >
        {picker && (
          <>
            {people.length === 0 && <p className="muted">Загружаю сотрудников…</p>}
            <AssignPicker
              key={picker.ids.join(",")}
              people={people}
              currentId={pickerList.length === 1 ? pickerList[0].suggested_employee_id : null}
              date={picker.mode === "confirm" ? pickerDate : null}
              withDate={picker.mode === "confirm"}
              withTime={picker.mode === "confirm" && pickerList.length === 1}
              busy={busy}
              onPick={(emp, day, time) => {
                const list = pickerList;
                const mode = picker.mode;
                setPicker(null);
                if (mode === "assign") void doAssign(list, emp);
                else void doConfirm(list, emp, day, time);
              }}
            />
            <p className="hint rq-picker-hint">
              {picker.mode === "confirm"
                ? "Наряд заведётся сразу, выезд встанет в день исполнителя. Пустой день — из заявки."
                : "Сначала — закреплённые за районом, дальше — по загрузке на день заявки."}
            </p>
          </>
        )}
      </Sheet>
    </div>
  );
}
