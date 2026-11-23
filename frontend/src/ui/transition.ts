/** Движение, которое отвечает на действие: переходы между разделами и вспышка подтверждения.
 *
 * withViewTransition(fn) — сменить экран через View Transitions API (Chrome/Edge/Safari 18);
 * где его нет или человек просит меньше движения — просто выполнить fn.
 * flashOk(el) — короткая зелёная вспышка строки: «сохранено, всё хорошо». */

import { flushSync } from "react-dom";

type ViewTransitionLike = { finished: Promise<void>; ready: Promise<void>; updateCallbackDone: Promise<void> };
type DocWithVT = Document & { startViewTransition?: (cb: () => void) => ViewTransitionLike };

const ignore = () => {};

export const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function withViewTransition(update: () => void): void {
  const doc = document as DocWithVT;
  if (!doc.startViewTransition || prefersReducedMotion() || document.visibilityState !== "visible") {
    update();
    return;
  }
  try {
    const vt = doc.startViewTransition(() => {
      flushSync(update);
    });
    // Новый переход до конца прошлого «пропускает» прошлый: его промисы отклоняются с AbortError.
    // Это штатно (быстрое переключение режимов), а не ошибка — гасим, чтобы не было unhandled rejection.
    vt.ready?.catch(ignore);
    vt.finished?.catch(ignore);
    vt.updateCallbackDone?.catch(ignore);
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
