/** Приборы таблицей быстрого ввода — «за стойкой», с клавиатуры и сканера.
 *
 * Строка: тип СИ (автодополнение по своему справочнику, новая строка берёт тип предыдущей,
 * последний тип помнится между приёмками), заводской №, год. Enter — следующая строка
 * (сканер штрихкода печатает номер и шлёт Enter — то же самое). Вставка из Excel/буфера
 * многими строками: столбец номеров или «тип ⇥ номер ⇥ год». По заводскому номеру — подсказка
 * «уже поверяли — прошлая поверка …» из своей базы (и тип одним нажатием). */

import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { fetchOwnHistory, fetchSiTypes, type OwnInstrumentHistory, type SiType } from "../../api";
import { normalize } from "../../ui/CommandPalette";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import type { BulkResult, BulkRow } from "./api";

export type TypeRef = { id: number; label: string };
type RowState = "new" | "saved" | "existing" | "error";
export type Row = {
  key: string;
  type: TypeRef | null;
  typeQuery: string;
  serial: string;
  year: string;
  state: RowState;
  error?: string;
  clientId: string;
};

const LAST_TYPE_KEY = "ei_doc_intake_last_type";
const uuid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => (Number(c) ^ (Math.random() * 16) >> (Number(c) / 4)).toString(16));

function lastType(): TypeRef | null {
  try {
    const raw = localStorage.getItem(LAST_TYPE_KEY);
    return raw ? (JSON.parse(raw) as TypeRef) : null;
  } catch {
    return null;
  }
}
function rememberType(type: TypeRef | null) {
  try {
    if (type) localStorage.setItem(LAST_TYPE_KEY, JSON.stringify(type));
  } catch {
    /* приватный режим */
  }
}

const newRow = (type: TypeRef | null): Row => ({ key: uuid(), type, typeQuery: "", serial: "", year: "", state: "new", clientId: uuid() });

// Справочник типов — один раз на сессию: ищем по нему локально, мгновенно.
let typesCache: Promise<SiType[]> | null = null;
function loadTypes(): Promise<SiType[]> {
  if (!typesCache) {
    typesCache = fetchSiTypes().catch((exc) => {
      typesCache = null;
      throw exc;
    });
  }
  return typesCache;
}

// История по заводскому номеру — кэш на сессию.
const historyCache = new Map<string, Promise<OwnInstrumentHistory[]>>();
function ownHistory(serial: string): Promise<OwnInstrumentHistory[]> {
  const key = serial.trim().toLowerCase();
  if (!historyCache.has(key)) {
    historyCache.set(key, fetchOwnHistory(serial.trim()).then((r) => r.own).catch(() => {
      historyCache.delete(key);
      return [];
    }));
  }
  return historyCache.get(key)!;
}

const YEAR = /^(19[5-9]\d|20[0-4]\d)$/;

function matchTypes(types: SiType[], query: string, limit = 8): SiType[] {
  const q = normalize(query);
  if (!q) return [];
  const words = q.split(" ");
  return types
    .map((t) => {
      const hay = normalize(`${t.label} ${t.name} ${t.registry_number} ${t.manufacturer}`);
      if (!words.every((w) => hay.includes(w))) return null;
      const score = (normalize(t.name).startsWith(q) ? 3 : 0) + (t.registry_number.startsWith(q) ? 3 : 0) + (t.needs_review ? 0 : 1);
      return { t, score };
    })
    .filter((x): x is { t: SiType; score: number } => !!x)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((x) => x.t);
}

const ruDay = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("ru-RU") : "");

export default function FastTable({
  onSave, saveLabel = "Сохранить приборы", busyExternal = false, onRowsChange, autoFocus = true,
}: {
  /** Сохранить заполненные строки пачкой; ответ bulk — по индексам переданных строк. */
  onSave: (rows: BulkRow[]) => Promise<BulkResult | null>;
  saveLabel?: string;
  busyExternal?: boolean;
  onRowsChange?: (filled: number) => void;
  autoFocus?: boolean;
}) {
  const [rows, setRows] = useState<Row[]>(() => [newRow(lastType())]);
  const [types, setTypes] = useState<SiType[]>([]);
  const [typesError, setTypesError] = useState("");
  const [focus, setFocus] = useState<{ row: number; col: "type" | "serial" | "year" } | null>(null);
  const [active, setActive] = useState(0);
  const [history, setHistory] = useState<Record<string, OwnInstrumentHistory[]>>({});
  const [busy, setBusy] = useState(false);
  const tableRef = useRef<HTMLTableElement>(null);

  useEffect(() => {
    loadTypes().then(setTypes).catch(() => setTypesError("Справочник типов не загрузился — проверьте связь"));
  }, []);

  const filled = rows.filter((r) => r.serial.trim() && r.state !== "saved" && r.state !== "existing");
  useEffect(() => {
    onRowsChange?.(filled.length);
  }, [filled.length, onRowsChange]);

  const focusCell = useCallback((row: number, col: "type" | "serial" | "year") => {
    window.requestAnimationFrame(() => {
      const el = tableRef.current?.querySelector<HTMLElement>(`[data-row="${row}"][data-col="${col}"]`);
      el?.focus();
      if (el instanceof HTMLInputElement) el.select();
    });
  }, []);

  useEffect(() => {
    if (autoFocus) focusCell(0, rows[0].type ? "serial" : "type");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const patch = (index: number, value: Partial<Row>) =>
    setRows((list) => list.map((r, i) => (i === index ? { ...r, ...value, state: value.state ?? (r.state === "error" ? "new" : r.state) } : r)));

  // Подсказка истории — после паузы в наборе номера.
  const serials = rows.map((r) => r.serial.trim()).join("\n");
  useEffect(() => {
    const timer = window.setTimeout(() => {
      for (const serial of new Set(serials.split("\n").filter((s) => s.length >= 4))) {
        if (history[serial] !== undefined) continue;
        void ownHistory(serial).then((own) => setHistory((h) => ({ ...h, [serial]: own })));
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [serials, history]);

  const pickType = (index: number, type: SiType) => {
    const ref = { id: type.id, label: type.label };
    patch(index, { type: ref, typeQuery: "" });
    rememberType(ref);
    focusCell(index, "serial");
  };

  const goNext = (index: number) => {
    const current = rows[index];
    if (index === rows.length - 1) {
      if (!current.serial.trim()) return;
      setRows((list) => [...list, newRow(current.type)]);
    }
    focusCell(index + 1, "serial");
  };

  const suggestions = useMemo(
    () => (focus?.col === "type" ? matchTypes(types, rows[focus.row]?.typeQuery ?? "") : []),
    [focus, types, rows],
  );

  const onTypeKey = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && suggestions.length) {
      event.preventDefault();
      setActive((i) => (i + 1) % suggestions.length);
    } else if (event.key === "ArrowUp" && suggestions.length) {
      event.preventDefault();
      setActive((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (event.key === "Enter" || event.key === "Tab") {
      if (suggestions[active]) {
        event.preventDefault();
        pickType(index, suggestions[active]);
      } else if (event.key === "Enter" && rows[index].type) {
        event.preventDefault();
        focusCell(index, "serial");
      }
    } else if (event.key === "Backspace" && !rows[index].typeQuery && rows[index].type) {
      patch(index, { type: null });
    }
  };

  const onCellKey = (index: number, col: "serial" | "year", event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (event.metaKey || event.ctrlKey) return void save();
      goNext(index);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (index < rows.length - 1) focusCell(index + 1, col);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (index > 0) focusCell(index - 1, col);
    } else if (event.key === "Backspace" && col === "serial" && !rows[index].serial && rows.length > 1) {
      event.preventDefault();
      setRows((list) => list.filter((_, i) => i !== index));
      focusCell(Math.max(0, index - 1), "serial");
    }
  };

  /** Вставка многими строками: «номер», «номер ⇥ год», «тип ⇥ номер ⇥ год». */
  const onPaste = (index: number, event: ClipboardEvent<HTMLInputElement>) => {
    const text = event.clipboardData.getData("text");
    if (!/[\n\t]/.test(text.trim())) return;
    event.preventDefault();
    const lines = text.replace(/\r/g, "").split("\n").map((l) => l.split("\t").map((c) => c.trim())).filter((cells) => cells.some(Boolean));
    setRows((list) => {
      const next = [...list];
      let at = index;
      let type = next[index]?.type ?? null;
      for (const cells of lines) {
        let year = "";
        let serial = "";
        let lineType: TypeRef | null = null;
        const rest: string[] = [];
        for (const cell of cells) {
          if (!cell) continue;
          if (!year && YEAR.test(cell)) year = cell;
          else rest.push(cell);
        }
        if (rest.length >= 2) {
          const found = matchTypes(types, rest[0], 1)[0];
          if (found) {
            lineType = { id: found.id, label: found.label };
            rest.shift();
          }
        }
        serial = rest[0] ?? "";
        if (!serial) continue;
        if (lineType) type = lineType;
        const row: Row = { ...(next[at] ?? newRow(type)), serial, year, type: lineType ?? next[at]?.type ?? type, state: "new" };
        if (at < next.length) next[at] = row;
        else next.push(row);
        at += 1;
      }
      if (next[next.length - 1].serial) next.push(newRow(next[next.length - 1].type));
      focusCell(next.length - 1, "serial");
      return next;
    });
  };

  const save = async () => {
    const indexes: number[] = [];
    const payload: BulkRow[] = [];
    const missing: number[] = [];
    rows.forEach((row, i) => {
      if (!row.serial.trim() || row.state === "saved" || row.state === "existing") return;
      if (!row.type) {
        missing.push(i);
        return;
      }
      indexes.push(i);
      payload.push({
        si_type_id: row.type.id,
        serial_number: row.serial.trim(),
        manufacture_year: YEAR.test(row.year) ? Number(row.year) : null,
        client_id: row.clientId,
      });
    });
    if (missing.length) {
      setRows((list) => list.map((r, i) => (missing.includes(i) ? { ...r, state: "error", error: "Выберите тип СИ" } : r)));
      focusCell(missing[0], "type");
      return;
    }
    if (!payload.length) return;
    setBusy(true);
    try {
      const result = await onSave(payload);
      if (!result) return;
      const created = new Set(result.created.map((v) => v.client_id));
      const errors = new Map(result.errors.map((e) => [e.index, e.error]));
      setRows((list) => {
        const next = list.map((r, i) => {
          const at = indexes.indexOf(i);
          if (at < 0) return r;
          if (errors.has(at)) return { ...r, state: "error" as const, error: errors.get(at) };
          return { ...r, state: created.has(r.clientId) ? ("saved" as const) : ("existing" as const), error: undefined };
        });
        const tail = next[next.length - 1];
        return tail.serial ? [...next, newRow(tail.type)] : next;
      });
    } finally {
      setBusy(false);
    }
  };

  const serialCount = new Map<string, number>();
  rows.forEach((r) => {
    const s = r.serial.trim().toLowerCase();
    if (s) serialCount.set(s, (serialCount.get(s) ?? 0) + 1);
  });

  return (
    <div className="it-fast">
      {typesError && <div className="it-hint fail-text">{typesError}</div>}
      <div className="table-wrap">
        <table className="it-table" ref={tableRef}>
          <thead>
            <tr>
              <th className="it-n">#</th>
              <th>Тип СИ</th>
              <th>Заводской №</th>
              <th className="it-year">Год</th>
              <th className="it-state" aria-label="Состояние" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const own = history[row.serial.trim()] ?? [];
              const last = own[0];
              const dup = (serialCount.get(row.serial.trim().toLowerCase()) ?? 0) > 1;
              const locked = row.state === "saved" || row.state === "existing";
              return (
                <tr key={row.key} className={`it-row is-${row.state}`}>
                  <td className="it-n data">{index + 1}</td>
                  <td className="it-type">
                    {row.type && !(focus?.row === index && focus.col === "type" && row.typeQuery) ? (
                      <button type="button" className="it-type-chip" disabled={locked} data-row={index} data-col="type"
                              onClick={() => { patch(index, { type: null }); focusCell(index, "type"); }}
                              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); focusCell(index, "serial"); } }}
                              title="Сменить тип">
                        {row.type.label}
                      </button>
                    ) : (
                      <input
                        data-row={index}
                        data-col="type"
                        value={row.typeQuery}
                        placeholder={index === 0 ? "Начните: СГВ, ВСКМ, 12345-06…" : "тип"}
                        autoComplete="off"
                        disabled={locked}
                        onFocus={() => { setFocus({ row: index, col: "type" }); setActive(0); }}
                        onBlur={() => window.setTimeout(() => setFocus((f) => (f?.row === index && f.col === "type" ? null : f)), 150)}
                        onChange={(e) => { patch(index, { typeQuery: e.target.value }); setActive(0); }}
                        onKeyDown={(e) => onTypeKey(index, e)}
                      />
                    )}
                    {focus?.row === index && focus.col === "type" && suggestions.length > 0 && (
                      <ul className="suggestions it-suggest" role="listbox">
                        {suggestions.map((t, i) => (
                          <li key={t.id} role="option" aria-selected={i === active} className={i === active ? "active" : ""}
                              onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pickType(index, t); }}>
                            <span>{t.label}</span>
                            {t.needs_review && <span className="sub"> · ждёт проверки</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                    {focus?.row === index && focus.col === "type" && row.typeQuery.trim().length >= 2 && suggestions.length === 0 && types.length > 0 && (
                      <div className="it-hint">Нет в справочнике — заведите тип в карточке приёмки («Добавить прибор»)</div>
                    )}
                  </td>
                  <td className="it-serial">
                    <input
                      className="data"
                      data-row={index}
                      data-col="serial"
                      value={row.serial}
                      disabled={locked}
                      placeholder={index === 0 ? "номер или сканер" : ""}
                      autoComplete="off"
                      spellCheck={false}
                      onFocus={() => setFocus({ row: index, col: "serial" })}
                      onChange={(e) => patch(index, { serial: e.target.value })}
                      onKeyDown={(e) => onCellKey(index, "serial", e)}
                      onPaste={(e) => onPaste(index, e)}
                    />
                    {dup && <div className="it-hint signal-text">номер повторяется в списке</div>}
                    {last && !locked && (
                      <div className="it-hint">
                        <Icon name="history" size={12} /> уже поверяли
                        {last.last?.verified_at ? ` — прошлая поверка ${ruDay(last.last.verified_at)}` : ""}
                        {last.last?.next_verification_date ? `, до ${ruDay(last.last.next_verification_date)}` : ""}
                        {` · ${last.si_type.label}`}
                        {last.owner ? ` · ${last.owner}` : ""}
                        {(!row.type || row.type.id !== last.si_type.id) && (
                          <button type="button" className="link sm" onClick={() => {
                            const ref = { id: last.si_type.id, label: last.si_type.label };
                            patch(index, { type: ref, year: row.year || (last.manufacture_year ? String(last.manufacture_year) : "") });
                            rememberType(ref);
                          }}>
                            взять тип{last.manufacture_year && !row.year ? " и год" : ""}
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="it-year">
                    <input
                      className="data"
                      data-row={index}
                      data-col="year"
                      value={row.year}
                      disabled={locked}
                      inputMode="numeric"
                      maxLength={4}
                      onFocus={() => setFocus({ row: index, col: "year" })}
                      onChange={(e) => patch(index, { year: e.target.value.replace(/\D/g, "").slice(0, 4) })}
                      onKeyDown={(e) => onCellKey(index, "year", e)}
                    />
                  </td>
                  <td className="it-state">
                    {row.state === "saved" && <Icon name="check" size={16} className="pass-text" />}
                    {row.state === "existing" && <span className="sub" title="Уже был в этой приёмке">был</span>}
                    {row.state === "error" && <span className="fail-text it-err" title={row.error}><Icon name="alert" size={15} /> {row.error}</span>}
                    {row.state === "new" && rows.length > 1 && (
                      <button type="button" className="icon-btn sm" aria-label="Убрать строку" tabIndex={-1}
                              onClick={() => setRows((list) => list.filter((_, i) => i !== index))}>
                        <Icon name="x" size={13} />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="it-fast-foot">
        <span className="sub kbd-hint">
          <Kbd>Enter</Kbd> следующая строка · <Kbd>↑</Kbd><Kbd>↓</Kbd> по столбцу · вставка столбца из Excel · <Kbd keys="mod+enter" /> сохранить
        </span>
        <span className="spacer" />
        <button type="button" className="primary" disabled={busy || busyExternal || filled.length === 0} onClick={() => void save()}>
          <Icon name="check" size={16} /> {busy || busyExternal ? "Сохраняю…" : `${saveLabel}${filled.length ? ` · ${filled.length}` : ""}`}
        </button>
      </div>
    </div>
  );
}
