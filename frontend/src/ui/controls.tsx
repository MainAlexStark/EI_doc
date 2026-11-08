/** Мелкие общие элементы: сегменты, переключатель, поиск, пустое состояние, действия в шапке. */

import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon, { type IconName } from "./Icon";

export function Segmented<T extends string>({
  value, options, onChange, size, className, ariaLabel,
}: {
  value: T;
  options: { value: T; label: ReactNode; count?: number; icon?: IconName }[];
  onChange: (value: T) => void;
  size?: "sm";
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={`segmented ${size ?? ""} ${className ?? ""}`} role="tablist" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          type="button"
          role="tab"
          aria-selected={value === option.value}
          key={option.value}
          className={value === option.value ? "active" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.icon && <Icon name={option.icon} size={15} />}
          <span>{option.label}</span>
          {option.count != null && option.count > 0 && <span className="seg-count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Switch({
  checked, onChange, label, hint, disabled, id,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <label className={`switch-row ${disabled ? "disabled" : ""}`}>
      <span className="switch-text">
        <span>{label}</span>
        {hint && <small>{hint}</small>}
      </span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        className="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

export function SearchField({
  value, onChange, placeholder, className, autoFocus,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
}) {
  return (
    <label className={`search-field ${className ?? ""}`}>
      <Icon name="search" size={16} />
      <input
        type="search"
        value={value}
        placeholder={placeholder ?? "Поиск"}
        onChange={(event) => onChange(event.target.value)}
        autoFocus={autoFocus}
      />
      {value && (
        <button type="button" className="search-clear" aria-label="Очистить" onClick={() => onChange("")}>
          <Icon name="x" size={12} strokeWidth={2.4} />
        </button>
      )}
    </label>
  );
}

export function EmptyState({
  icon = "inbox", title, children, action,
}: {
  icon?: IconName;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon name={icon} size={26} /></div>
      <div className="empty-title">{title}</div>
      {children && <div className="empty-text">{children}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {label ?? "Загружаю…"}
    </div>
  );
}

export function Alert({ tone = "error", children, onClose }: { tone?: "error" | "warn" | "info" | "ok"; children: ReactNode; onClose?: () => void }) {
  const icon: IconName = tone === "ok" ? "checkCircle" : tone === "info" ? "info" : "alert";
  return (
    <div className={`alert ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon name={icon} size={17} />
      <div className="alert-text">{children}</div>
      {onClose && (
        <button type="button" className="icon-btn sm" onClick={onClose} aria-label="Скрыть">
          <Icon name="x" size={14} />
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Действия страницы — кнопки справа от заголовка в шапке App.

export const PageActionsContext = createContext<HTMLElement | null>(null);

export function PageActions({ children }: { children: ReactNode }) {
  const target = useContext(PageActionsContext);
  return target ? createPortal(children, target) : null;
}
