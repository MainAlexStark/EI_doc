/** Модальные окна и системные диалоги приложения.
 *
 * <Modal> — окно поверх страницы (на телефоне — лист снизу на весь экран).
 * confirmDialog()/promptDialog() — замена window.confirm/prompt в стиле интерфейса;
 * работают, пока в дереве смонтирован <DialogHost/> (он в App и в публичной форме).
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  /** Без внутренних отступов тела — для окон со своей раскладкой (контекст ассистента). */
  bare?: boolean;
  className?: string;
};

let openCount = 0;

export function Modal({ open, onClose, title, subtitle, children, footer, size = "md", bare, className }: ModalProps) {
  const titleId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    openCount += 1;
    document.body.classList.add("modal-open");
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    // Фокус внутрь окна — но не на крестик, а на первое поле, если оно есть.
    const timer = window.setTimeout(() => {
      const card = cardRef.current;
      if (!card || card.contains(document.activeElement)) return;
      const field = card.querySelector<HTMLElement>("[autofocus], input:not([type=hidden]):not([disabled]), textarea, select");
      (field ?? card).focus({ preventScroll: true });
    }, 30);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
      openCount -= 1;
      if (openCount === 0) document.body.classList.remove("modal-open");
      previous?.focus?.({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className="modal-layer" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={cardRef}
        className={`modal-card modal-${size} ${bare ? "bare" : ""} ${className ?? ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
      >
        {title && (
          <header className="modal-head">
            <div className="modal-titles">
              <h2 id={titleId}>{title}</h2>
              {subtitle && <div className="modal-sub">{subtitle}</div>}
            </div>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Закрыть">
              <Icon name="x" />
            </button>
          </header>
        )}
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// confirm / prompt

type DialogRequest =
  | { kind: "confirm"; title: string; text?: string; ok: string; danger?: boolean; resolve: (v: boolean) => void }
  | {
      kind: "prompt"; title: string; text?: string; ok: string; value: string; placeholder?: string;
      multiline?: boolean; resolve: (v: string | null) => void;
    };

let push: ((request: DialogRequest) => void) | null = null;

export function confirmDialog(options: { title: string; text?: string; ok?: string; danger?: boolean }): Promise<boolean> {
  if (!push) return Promise.resolve(window.confirm(options.title));
  return new Promise((resolve) => push!({ kind: "confirm", ok: options.danger ? "Удалить" : "Да", ...options, resolve }));
}

export function promptDialog(options: {
  title: string; text?: string; ok?: string; value?: string; placeholder?: string; multiline?: boolean;
}): Promise<string | null> {
  if (!push) return Promise.resolve(window.prompt(options.title, options.value ?? ""));
  return new Promise((resolve) =>
    push!({ kind: "prompt", ok: "Готово", value: "", ...options, resolve }),
  );
}

export function DialogHost() {
  const [queue, setQueue] = useState<DialogRequest[]>([]);
  const [value, setValue] = useState("");
  useEffect(() => {
    push = (request) => setQueue((q) => [...q, request]);
    return () => {
      push = null;
    };
  }, []);
  const current = queue[0];
  useEffect(() => {
    if (current?.kind === "prompt") setValue(current.value);
  }, [current]);
  if (!current) return null;

  const finish = (answer: boolean) => {
    if (current.kind === "confirm") current.resolve(answer);
    else current.resolve(answer ? value : null);
    setQueue((q) => q.slice(1));
  };

  return (
    <Modal
      open
      size="sm"
      onClose={() => finish(false)}
      title={current.title}
      footer={
        <>
          <button type="button" onClick={() => finish(false)}>Отмена</button>
          <button
            type="button"
            className={current.kind === "confirm" && current.danger ? "danger-fill" : "primary"}
            onClick={() => finish(true)}
            autoFocus={current.kind === "confirm"}
          >
            {current.ok}
          </button>
        </>
      }
    >
      <form
        className="dialog-body"
        onSubmit={(event) => {
          event.preventDefault();
          finish(true);
        }}
      >
        {current.text && <p className="dialog-text">{current.text}</p>}
        {current.kind === "prompt" &&
          (current.multiline ? (
            <textarea rows={3} value={value} placeholder={current.placeholder} onChange={(e) => setValue(e.target.value)} autoFocus />
          ) : (
            <input value={value} placeholder={current.placeholder} onChange={(e) => setValue(e.target.value)} autoFocus />
          ))}
      </form>
    </Modal>
  );
}
