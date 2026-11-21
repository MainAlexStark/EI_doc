/** Горячие клавиши.
 *
 *   useHotkey("n", () => setCreating(true), { description: "Новая приёмка", group: "Приёмки" });
 *   useHotkey("mod+k", openPalette, { allowInInputs: true });
 *   useHotkey(["j", "arrowdown"], next, { description: "Следующая заявка" });
 *
 * Комбинация: клавиши через «+»; mod — ⌘ на Mac, Ctrl на остальных. Сравнивается event.key
 * (без регистра), а для букв/цифр ещё и event.code — так «n» срабатывает и в русской раскладке.
 * В полях ввода по умолчанию не срабатывает (кроме mod-комбинаций при allowInInputs).
 * Пока открыто модальное окно / лист, клавиши без модификаторов молчат (кроме Escape).
 * Всё, у чего есть description, видно в справке по «?» (<HotkeyHelp/> в App). */

import { useEffect, useRef, useSyncExternalStore } from "react";
import { Modal } from "./Modal";
import Kbd from "./Kbd";

export type HotkeyOptions = {
  enabled?: boolean;
  allowInInputs?: boolean;
  description?: string;
  group?: string;
  /** Срабатывать и при открытом модальном окне. */
  global?: boolean;
  preventDefault?: boolean;
};

type Registered = { id: number; combos: string[]; description: string; group: string };
let registered: Registered[] = [];
const listeners = new Set<() => void>();
let seq = 0;
const emit = () => listeners.forEach((listener) => listener());

export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (el as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"].includes(type);
  }
  return false;
}

const CODE_FOR: Record<string, string> = {};
"abcdefghijklmnopqrstuvwxyz".split("").forEach((ch) => (CODE_FOR[ch] = `Key${ch.toUpperCase()}`));
"0123456789".split("").forEach((ch) => (CODE_FOR[ch] = `Digit${ch}`));

export function matchesCombo(event: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split("+");
  const key = parts.pop() ?? "";
  const wantMod = parts.includes("mod");
  const wantCtrl = parts.includes("ctrl");
  const wantAlt = parts.includes("alt");
  const wantShift = parts.includes("shift");
  const hasMod = event.metaKey || event.ctrlKey;
  if (wantMod !== hasMod && !wantCtrl) return false;
  if (wantCtrl && !event.ctrlKey) return false;
  if (wantAlt !== event.altKey) return false;
  // «?» и «/» сами по себе зависят от Shift — его не требуем и не запрещаем.
  const shiftFree = key === "?" || key.length === 1 && !/[a-z0-9]/.test(key);
  if (!shiftFree && wantShift !== event.shiftKey) return false;
  const pressed = event.key.toLowerCase();
  if (pressed === key) return true;
  if (key === "esc" && pressed === "escape") return true;
  const code = CODE_FOR[key];
  if (code && event.code === code) return true;
  if (key === "?" && event.code === "Slash" && event.shiftKey) return true;
  if (key === "/" && event.code === "Slash" && !event.shiftKey) return true;
  return false;
}

const modalOpen = () => document.body.classList.contains("modal-open") || !!document.querySelector(".sheet-layer:not(.modeless):not(.closing), .palette-layer");

export function useHotkey(
  combo: string | string[],
  handler: (event: KeyboardEvent) => void,
  options: HotkeyOptions = {},
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  const { enabled = true, allowInInputs = false, description, group = "Общее", global = false, preventDefault = true } = options;
  const combos = Array.isArray(combo) ? combo : [combo];
  const key = combos.join("|");

  useEffect(() => {
    if (!enabled) return;
    const list = key.split("|");
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (!list.some((one) => matchesCombo(event, one))) return;
      const withMod = event.metaKey || event.ctrlKey || event.altKey;
      if (isTypingTarget(event.target) && !(allowInInputs && withMod) && !(allowInInputs && event.key === "Escape")) return;
      if (!global && !withMod && event.key !== "Escape" && modalOpen()) return;
      if (preventDefault) event.preventDefault();
      handlerRef.current(event);
    };
    window.addEventListener("keydown", onKey);
    let id = 0;
    if (description) {
      id = ++seq;
      registered = [...registered, { id, combos: list, description, group }];
      emit();
    }
    return () => {
      window.removeEventListener("keydown", onKey);
      if (id) {
        registered = registered.filter((item) => item.id !== id);
        emit();
      }
    };
  }, [key, enabled, allowInInputs, description, group, global, preventDefault]);
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => registered;

/** Справка по клавишам («?»). Показывает всё зарегистрированное с description, по группам. */
export function HotkeyHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const all = useSyncExternalStore(subscribe, snapshot, snapshot);
  const groups = new Map<string, Registered[]>();
  const seen = new Set<string>();
  for (const item of all) {
    const sig = `${item.group}|${item.description}`;
    if (seen.has(sig)) continue;
    seen.add(sig);
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group)!.push(item);
  }
  return (
    <Modal open={open} onClose={onClose} title="Клавиши" subtitle="Всё делается с клавиатуры. В полях ввода буквы печатаются как обычно." size="md">
      <div className="hotkey-help">
        {[...groups.entries()].map(([group, list]) => (
          <section key={group}>
            <h3>{group}</h3>
            {list.map((item) => (
              <div className="hotkey-row" key={item.id}>
                <span>{item.description}</span>
                <span className="hotkey-keys">
                  {item.combos.length > 3 ? (
                    <>
                      <Kbd keys={item.combos[0]} />
                      <span className="muted small">…</span>
                      <Kbd keys={item.combos[item.combos.length - 1]} />
                    </>
                  ) : (
                    item.combos.map((one, index) => (
                      <span key={one}>
                        {index > 0 && <span className="muted small"> или </span>}
                        <Kbd keys={one} />
                      </span>
                    ))
                  )}
                </span>
              </div>
            ))}
          </section>
        ))}
      </div>
    </Modal>
  );
}
