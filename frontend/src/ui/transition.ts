/** Движение, которое отвечает на действие: переходы между разделами и вспышка подтверждения.
 *
 * withViewTransition(fn) — сменить экран через View Transitions API (Chrome/Edge/Safari 18);
 * где его нет или человек просит меньше движения — просто выполнить fn.
 * flashOk(el) — короткая зелёная вспышка строки: «сохранено, всё хорошо». */

import { flushSync } from "react-dom";

type DocWithVT = Document & { startViewTransition?: (cb: () => void) => { finished: Promise<void> } };

export const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function withViewTransition(update: () => void): void {
  const doc = document as DocWithVT;
  if (!doc.startViewTransition || prefersReducedMotion() || document.visibilityState !== "visible") {
    update();
    return;
  }
  try {
    doc.startViewTransition(() => {
      flushSync(update);
    });
  } catch {
    update();
  }
}

/** Вспышка подтверждения строки (класс .flash-ok, см. ui/shell.css). Можно передать селектор. */
export function flashOk(target: Element | string | null | undefined, tone: "ok" | "info" = "ok"): void {
  const el = typeof target === "string" ? document.querySelector(target) : target;
  if (!el) return;
  const cls = tone === "ok" ? "flash-ok" : "flash-info";
  el.classList.remove(cls);
  // перезапуск анимации, если строку подтверждают второй раз подряд
  void (el as HTMLElement).offsetWidth;
  el.classList.add(cls);
  window.setTimeout(() => el.classList.remove(cls), 1300);
}
