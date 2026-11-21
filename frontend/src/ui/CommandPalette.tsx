/** Палитра команд и поиск по всему — ⌘K / Ctrl+K или «/».
 *
 * Два вида содержимого:
 *  - команды (переходы, действия) — синхронный список, фильтруется по тексту на месте:
 *      useCommands("intake", [{ id: "intake:new", title: "Новая приёмка", icon: "box", run }]);
 *      // или вне React: const off = registerCommands("id", items);
 *  - источники поиска — асинхронные, по сущностям режима (заявки, протоколы, закупки…):
 *      registerCommandSource("requests", async (query, signal) => items, { group: "Заявки", minQuery: 2 });
 *    Источник вызывается с задержкой 160 мс после ввода; ошибки молча пропускаются.
 *
 * openCommandPalette("12345") — открыть программно, сразу с запросом. */

import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon, { type IconName } from "./Icon";
import Kbd from "./Kbd";
import { useHotkey } from "./Hotkeys";

export type CommandItem = {
  id: string;
  title: string;
  subtitle?: string;
  icon?: IconName;
  /** Заголовок группы в списке; у источника по умолчанию — его group. */
  group?: string;
  /** Доп. слова для поиска (синонимы): «приём приборов коробка». */
  keywords?: string;
  /** Комбинация клавиш для подсказки справа: "4", "mod+k". */
  hint?: string;
  /** Подзаголовок — данными (моно): заводской номер, номер протокола. */
  mono?: boolean;
  run: () => void;
};

export type CommandSource = (query: string, signal: AbortSignal) => Promise<CommandItem[]> | CommandItem[];
type SourceEntry = { id: string; source: CommandSource; group: string; minQuery: number; limit: number; order: number };

// --- реестр --------------------------------------------------------------
let commandSets = new Map<string, CommandItem[]>();
let sources = new Map<string, SourceEntry>();
let version = 0;
const listeners = new Set<() => void>();
const bump = () => {
  version += 1;
  listeners.forEach((listener) => listener());
};
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const getVersion = () => version;

export function registerCommands(id: string, items: CommandItem[]): () => void {
  commandSets = new Map(commandSets).set(id, items);
  bump();
  return () => {
    if (commandSets.get(id) !== items) return;
    commandSets = new Map(commandSets);
    commandSets.delete(id);
    bump();
  };
}

export function registerCommandSource(
  id: string,
  source: CommandSource,
  options: { group?: string; minQuery?: number; limit?: number; order?: number } = {},
): () => void {
  const entry: SourceEntry = {
    id, source, group: options.group ?? id, minQuery: options.minQuery ?? 2, limit: options.limit ?? 6, order: options.order ?? 50,
  };
  sources = new Map(sources).set(id, entry);
  bump();
  return () => {
    if (sources.get(id) !== entry) return;
    sources = new Map(sources);
    sources.delete(id);
    bump();
  };
}

/** Команды, пока компонент смонтирован. items пересобираются при смене deps. */
export function useCommands(id: string, items: CommandItem[], deps: unknown[] = []): void {
  const ref = useRef(items);
  ref.current = items;
  useEffect(() => registerCommands(id, ref.current), [id, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
}

let openHandler: ((query?: string) => void) | null = null;
export const openCommandPalette = (query?: string) => openHandler?.(query);

// --- поиск ---------------------------------------------------------------
export const normalize = (text: string) => text.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();

/** 0 — не подходит; больше — лучше. Каждое слово запроса должно найтись. */
export function scoreText(haystack: string, query: string): number {
  const hay = normalize(haystack);
  const words = normalize(query).split(" ").filter(Boolean);
  if (!words.length) return 1;
  let score = 0;
  for (const word of words) {
    const at = hay.indexOf(word);
    if (at < 0) return 0;
    score += at === 0 ? 30 : /[\s«"(.,-]/.test(hay[at - 1] ?? "") ? 20 : 8;
  }
  return score;
}

type Row = CommandItem & { _group: string };

// --- компонент -----------------------------------------------------------
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [remote, setRemote] = useState<Record<string, CommandItem[]>>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  useSyncExternalStore(subscribe, getVersion, getVersion);

  useEffect(() => {
    openHandler = (initial) => {
      setQuery(initial ?? "");
      setActive(0);
      setOpen(true);
    };
    return () => {
      openHandler = null;
    };
  }, []);

  useHotkey("mod+k", () => (open ? setOpen(false) : openHandler?.()), {
    allowInInputs: true, global: true, description: "Палитра команд и поиск", group: "Общее",
  });
  useHotkey("/", () => openHandler?.(), { description: "Поиск по всему", group: "Общее" });

  // асинхронные источники
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    const entries = [...sources.values()].filter((entry) => q.length >= entry.minQuery);
    setRemote({});
    if (!entries.length) {
      setLoading(new Set());
      return;
    }
    const controller = new AbortController();
    setLoading(new Set(entries.map((entry) => entry.id)));
    const timer = window.setTimeout(() => {
      for (const entry of entries) {
        Promise.resolve()
          .then(() => entry.source(q, controller.signal))
          .then((items) => {
            if (controller.signal.aborted) return;
            setRemote((current) => ({ ...current, [entry.id]: items.slice(0, entry.limit) }));
          })
          .catch(() => undefined)
          .finally(() => {
            if (controller.signal.aborted) return;
            setLoading((current) => {
              const next = new Set(current);
              next.delete(entry.id);
              return next;
            });
          });
      }
    }, 160);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const rows: Row[] = useMemo(() => {
    const q = query.trim();
    const local: (Row & { _score: number })[] = [];
    for (const items of commandSets.values()) {
      for (const item of items) {
        const score = q ? scoreText(`${item.title} ${item.subtitle ?? ""} ${item.keywords ?? ""}`, q) : 1;
        if (score > 0) local.push({ ...item, _group: item.group ?? "Команды", _score: score });
      }
    }
    if (q) local.sort((a, b) => b._score - a._score);
    const limitedLocal = q ? local.slice(0, 8) : local;
    const fromSources: Row[] = [...sources.values()]
      .sort((a, b) => a.order - b.order)
      .flatMap((entry) => (remote[entry.id] ?? []).map((item) => ({ ...item, _group: item.group ?? entry.group })));
    // без запроса — группы в порядке регистрации; с запросом — сначала лучшие команды, потом данные
    const all = [...limitedLocal, ...fromSources];
    const order: string[] = [];
    for (const row of all) if (!order.includes(row._group)) order.push(row._group);
    return order.flatMap((group) => all.filter((row) => row._group === group));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, remote, version]);

  useEffect(() => {
    setActive((current) => Math.min(current, Math.max(rows.length - 1, 0)));
  }, [rows.length]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 10);
    return () => {
      window.clearTimeout(timer);
      previous?.focus?.({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;

  const close = () => setOpen(false);
  const choose = (row: Row | undefined) => {
    if (!row) return;
    close();
    // после закрытия — чтобы фокус и переход не спорили с палитрой
    window.setTimeout(() => row.run(), 0);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => (rows.length ? (index + 1) % rows.length : 0));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => (rows.length ? (index - 1 + rows.length) % rows.length : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(rows[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Home" && event.ctrlKey) {
      setActive(0);
    }
  };

  let lastGroup = "";
  const body: ReactNode[] = [];
  rows.forEach((row, index) => {
    if (row._group !== lastGroup) {
      lastGroup = row._group;
      body.push(<div className="palette-group" key={`g-${row._group}`} role="presentation">{row._group}</div>);
    }
    body.push(
      <div
        key={`${row.id}-${index}`}
        id={`${listId}-${index}`}
        data-index={index}
        role="option"
        aria-selected={index === active}
        className={`palette-item ${index === active ? "active" : ""}`}
        onMouseMove={() => index !== active && setActive(index)}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => choose(row)}
      >
        <span className="palette-icon"><Icon name={row.icon ?? "arrowRight"} size={17} /></span>
        <span className="palette-text">
          <span className="palette-title">{row.title}</span>
          {row.subtitle && <span className={`palette-sub ${row.mono ? "mono" : ""}`}>{row.subtitle}</span>}
        </span>
        {row.hint && <Kbd keys={row.hint} className="kbd-hint" />}
        {index === active && <Icon name="arrowRight" size={15} className="palette-go" />}
      </div>,
    );
  });

  const busy = loading.size > 0;
  return createPortal(
    <div className="palette-layer" onMouseDown={(event) => event.target === event.currentTarget && close()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Палитра команд">
        <div className="palette-input">
          <Icon name="search" size={18} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Куда перейти, что сделать, что найти — № заявки, заводской номер, заказчик"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={rows.length ? `${listId}-${active}` : undefined}
            autoComplete="off"
            spellCheck={false}
            enterKeyHint="go"
          />
          {busy && <span className="spinner sm" aria-label="Ищу" />}
          <button type="button" className="palette-esc" onClick={close} aria-label="Закрыть">Esc</button>
        </div>
        <div className="palette-list" id={listId} role="listbox" ref={listRef}>
          {body}
          {!rows.length && !busy && (
            <div className="palette-empty">
              {query.trim().length < 2 ? "Начните печатать" : <>Ничего не нашлось по «{query.trim()}»</>}
            </div>
          )}
        </div>
        <div className="palette-foot kbd-hint">
          <span><Kbd keys="arrowup" /><Kbd keys="arrowdown" /> выбрать</span>
          <span><Kbd keys="enter" /> открыть</span>
          <span><Kbd keys="esc" /> закрыть</span>
          <span className="spacer" />
          <span><Kbd keys="?" /> все клавиши</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default CommandPalette;
