/** Клавиша: <Kbd>⌘K</Kbd>, <Kbd keys="mod+k" /> — подпись под платформу (⌘ на Mac, Ctrl на остальных). */

import type { ReactNode } from "react";

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent);

const LABELS: Record<string, string> = {
  mod: isMac ? "⌘" : "Ctrl",
  ctrl: "Ctrl",
  alt: isMac ? "⌥" : "Alt",
  shift: isMac ? "⇧" : "Shift",
  enter: "↵",
  escape: "Esc",
  esc: "Esc",
  arrowup: "↑",
  arrowdown: "↓",
  arrowleft: "←",
  arrowright: "→",
  space: "Пробел",
  backspace: "⌫",
};

/** «mod+k» → ["⌘", "K"] */
export function keyLabels(combo: string): string[] {
  return combo.split("+").map((part) => LABELS[part.toLowerCase()] ?? (part.length === 1 ? part.toUpperCase() : part));
}

export default function Kbd({ children, keys, className }: { children?: ReactNode; keys?: string; className?: string }) {
  if (keys) {
    return (
      <span className={`kbd-group ${className ?? ""}`}>
        {keyLabels(keys).map((label, index) => (
          <kbd key={index} className="kbd">{label}</kbd>
        ))}
      </span>
    );
  }
  return <kbd className={`kbd ${className ?? ""}`}>{children}</kbd>;
}
