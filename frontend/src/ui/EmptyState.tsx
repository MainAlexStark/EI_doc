/** Пустой экран — приглашение к действию: иконка, что здесь будет, и одна кнопка.
 *
 * <EmptyState icon="box" title="Приёмок пока нет" action={{ label: "Новая приёмка", icon: "plus", onClick }}>
 *   Клиент принёс приборы — заведите приёмку.
 * </EmptyState>
 *
 * action можно передать и готовой разметкой (ReactNode) — так его использовали старые экраны. */

import { isValidElement, type ReactNode } from "react";
import Icon, { type IconName } from "./Icon";
import Kbd from "./Kbd";

export type EmptyAction = { label: string; onClick: () => void; icon?: IconName; hotkey?: string };

export function EmptyState({
  icon = "inbox", title, children, action, secondary, tone, compact,
}: {
  icon?: IconName;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode | EmptyAction;
  secondary?: EmptyAction;
  /** error — «что случилось и что сделать». */
  tone?: "error";
  compact?: boolean;
}) {
  const isActionObject = (value: unknown): value is EmptyAction =>
    !!value && typeof value === "object" && !isValidElement(value) && "onClick" in (value as object);
  return (
    <div className={`empty-state ${tone === "error" ? "is-error" : ""} ${compact ? "compact" : ""}`}>
      <div className="empty-icon"><Icon name={tone === "error" ? "alert" : icon} size={26} /></div>
      <div className="empty-title">{title}</div>
      {children && <div className="empty-text">{children}</div>}
      {(action || secondary) && (
        <div className="empty-action">
          {isActionObject(action) ? (
            <button type="button" className="primary" onClick={action.onClick}>
              {action.icon && <Icon name={action.icon} size={16} />}
              {action.label}
              {action.hotkey && <Kbd keys={action.hotkey} className="on-accent kbd-hint" />}
            </button>
          ) : (
            action
          )}
          {secondary && (
            <button type="button" onClick={secondary.onClick}>
              {secondary.icon && <Icon name={secondary.icon} size={16} />}
              {secondary.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default EmptyState;
