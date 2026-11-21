/** Тосты — короткие сообщения о результате действия, с «Отменить» там, где действие обратимо.
 *
 *   toast.success("Приёмка создана");
 *   toast.error("Не удалось сохранить", { description: "Нет связи — повторите позже" });
 *   toast.info("Заявка назначена", { undo: () => api.unassign(id) });
 *   toast.success("Сохранено", { action: { label: "Открыть", onClick: () => go("journal") } });
 *   const id = toast.info("Загружаю…", { duration: 0 }); … toast.dismiss(id);
 *
 * Работает, пока в дереве смонтирован <Toaster/> (он в App). Одновременно видно до трёх,
 * остальные ждут в очереди; наведение мышью останавливает таймер. */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import Icon, { type IconName } from "./Icon";

export type ToastTone = "success" | "error" | "info" | "warn";
export type ToastOptions = {
  description?: string;
  /** Кнопка «Отменить»: вызывается, тост закрывается. */
  undo?: () => void | Promise<void>;
  undoLabel?: string;
  action?: { label: string; onClick: () => void };
  /** мс; 0 — пока не закроют. По умолчанию 4.5 с, с «Отменить» — 7 с, ошибка — 7 с. */
  duration?: number;
  id?: string;
};
export type ToastItem = ToastOptions & { id: string; tone: ToastTone; message: string; createdAt: number };

let items: ToastItem[] = [];
const listeners = new Set<() => void>();
let seq = 0;
const emit = () => listeners.forEach((listener) => listener());
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => items;

function show(tone: ToastTone, message: string, options: ToastOptions = {}): string {
  const id = options.id ?? `t${++seq}`;
  const item: ToastItem = { ...options, id, tone, message, createdAt: Date.now() };
  // Тот же id — заменить (обновить «Загружаю…» на «Готово»), а не плодить.
  items = items.some((existing) => existing.id === id)
    ? items.map((existing) => (existing.id === id ? item : existing))
    : [...items, item];
  emit();
  return id;
}

function dismiss(id?: string) {
  items = id ? items.filter((item) => item.id !== id) : [];
  emit();
}

export const toast = Object.assign((message: string, options?: ToastOptions) => show("info", message, options), {
  success: (message: string, options?: ToastOptions) => show("success", message, options),
  error: (message: string, options?: ToastOptions) => show("error", message, options),
  info: (message: string, options?: ToastOptions) => show("info", message, options),
  warn: (message: string, options?: ToastOptions) => show("warn", message, options),
  dismiss,
});

const ICON: Record<ToastTone, IconName> = { success: "checkCircle", error: "alert", info: "info", warn: "alert" };
const VISIBLE = 3;

export function Toaster() {
  const all = useSyncExternalStore(subscribe, snapshot, snapshot);
  const visible = all.slice(0, VISIBLE);
  const queued = all.length - visible.length;
  return createPortal(
    <div className="toaster" aria-live="polite" aria-relevant="additions">
      {visible.map((item) => (
        <ToastView key={item.id} item={item} />
      ))}
      {queued > 0 && <div className="toast-queue">ещё {queued}</div>}
    </div>,
    document.body,
  );
}

function ToastView({ item }: { item: ToastItem }) {
  const [paused, setPaused] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const remaining = useRef(
    item.duration ?? (item.undo || item.tone === "error" ? 7000 : 4500),
  );
  const started = useRef(Date.now());

  const close = () => {
    setLeaving(true);
    window.setTimeout(() => dismiss(item.id), 160);
  };

  useEffect(() => {
    remaining.current = item.duration ?? (item.undo || item.tone === "error" ? 7000 : 4500);
  }, [item.createdAt, item.duration, item.undo, item.tone]);

  useEffect(() => {
    if (paused || remaining.current <= 0) return;
    started.current = Date.now();
    const timer = window.setTimeout(close, remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current -= Date.now() - started.current;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, item.createdAt]);

  return (
    <div
      className={`toast ${item.tone} ${leaving ? "leaving" : ""}`}
      role={item.tone === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <Icon name={ICON[item.tone]} size={18} className="toast-icon" />
      <div className="toast-body">
        <div className="toast-title">{item.message}</div>
        {item.description && <div className="toast-desc">{item.description}</div>}
      </div>
      {item.undo && (
        <button
          type="button"
          className="toast-btn"
          onClick={() => {
            void Promise.resolve(item.undo?.()).catch(() => toast.error("Не получилось отменить"));
            close();
          }}
        >
          <Icon name="undo" size={15} /> {item.undoLabel ?? "Отменить"}
        </button>
      )}
      {item.action && (
        <button
          type="button"
          className="toast-btn"
          onClick={() => {
            item.action?.onClick();
            close();
          }}
        >
          {item.action.label}
        </button>
      )}
      <button type="button" className="icon-btn sm toast-close" aria-label="Закрыть" onClick={close}>
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}

export default toast;
