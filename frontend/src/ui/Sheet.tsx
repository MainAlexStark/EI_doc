/** Лист — панель, которая выезжает сбоку (компьютер) или снизу (телефон).
 *  Для карточки без ухода со списка: заявка, прибор, адрес, документ закупки.
 *
 *   <Sheet open={!!id} onClose={() => setId(null)} title="Заявка №12" side="auto"
 *          footer={<button className="primary">Назначить</button>}>
 *     …
 *   </Sheet>
 *
 * side: "right" | "bottom" | "auto" (справа на компьютере, снизу на телефоне).
 * Esc и щелчок по подложке закрывают; фокус уходит внутрь и возвращается обратно. */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon";
import { PHONE, useMediaQuery } from "./hooks";

export type SheetProps = {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  side?: "right" | "bottom" | "auto";
  size?: "sm" | "md" | "lg";
  /** Без затемнения — лист рядом со списком, по которому можно продолжать щёлкать. */
  modeless?: boolean;
  className?: string;
};

const EXIT_MS = 200;

export function Sheet({
  open, onClose, title, subtitle, children, footer, side = "auto", size = "md", modeless, className,
}: SheetProps) {
  const phone = useMediaQuery(PHONE);
  const resolved = side === "auto" ? (phone ? "bottom" : "right") : side;
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const titleId = useId();

  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return;
    }
    if (!mounted) return;
    setClosing(true);
    const timer = window.setTimeout(() => {
      setMounted(false);
      setClosing(false);
    }, EXIT_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.body.classList.contains("modal-open")) {
        event.stopPropagation();
        closeRef.current();
      }
      if (event.key === "Tab" && panelRef.current && !modeless) {
        const focusable = panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    const timer = window.setTimeout(() => panelRef.current?.focus({ preventScroll: true }), 30);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
      previous?.focus?.({ preventScroll: true });
    };
  }, [open, modeless]);

  if (!mounted) return null;
  return createPortal(
    <div className={`sheet-layer ${resolved} ${modeless ? "modeless" : ""} ${closing ? "closing" : ""}`}>
      {!modeless && <div className="sheet-scrim" onMouseDown={() => onClose()} />}
      <div
        ref={panelRef}
        className={`sheet sheet-${size} ${className ?? ""}`}
        role="dialog"
        aria-modal={modeless ? undefined : true}
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
      >
        {resolved === "bottom" && <div className="sheet-grip" aria-hidden="true" />}
        {(title || subtitle) && (
          <header className="sheet-head">
            <div className="grow">
              {title && <h2 id={titleId}>{title}</h2>}
              {subtitle && <div className="sheet-sub">{subtitle}</div>}
            </div>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Закрыть">
              <Icon name="x" />
            </button>
          </header>
        )}
        <div className="sheet-body">{children}</div>
        {footer && <footer className="sheet-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export default Sheet;
