/** Шина действий режимов: «Новая приёмка» из палитры команд, клавиша N, большая
 *  кнопка на телефоне — всё это просит режим что-то сделать, не зная его внутренностей.
 *
 *  Режим слушает:   useModeAction("intake", { new: () => setCreating(true) });
 *  Кто угодно шлёт: emitModeAction("intake", "new");
 *
 *  Если режим ещё не смонтирован (переход + lazy-чанк), действие ждёт его до 8 секунд. */

import { useEffect, useRef } from "react";

type Handler = (action: string, payload?: unknown) => boolean;
const handlers = new Map<string, Set<Handler>>();
const pending = new Map<string, { action: string; payload?: unknown; at: number }>();
const TTL = 8000;

export function emitModeAction(mode: string, action: string, payload?: unknown): void {
  const set = handlers.get(mode);
  if (set) for (const handler of set) if (handler(action, payload)) return;
  pending.set(mode, { action, payload, at: Date.now() });
}

export function useModeAction(
  mode: string,
  actions: Record<string, ((payload?: unknown) => void) | undefined>,
  enabled = true,
): void {
  const ref = useRef(actions);
  ref.current = actions;
  useEffect(() => {
    if (!enabled) return;
    const handler: Handler = (action, payload) => {
      const fn = ref.current[action];
      if (!fn) return false;
      fn(payload);
      return true;
    };
    let set = handlers.get(mode);
    if (!set) handlers.set(mode, (set = new Set()));
    set.add(handler);
    const waiting = pending.get(mode);
    let timer: number | undefined;
    if (waiting && Date.now() - waiting.at < TTL && ref.current[waiting.action]) {
      pending.delete(mode);
      timer = window.setTimeout(() => ref.current[waiting.action]?.(waiting.payload), 0);
    }
    return () => {
      if (timer) window.clearTimeout(timer);
      set?.delete(handler);
    };
  }, [mode, enabled]);
}
