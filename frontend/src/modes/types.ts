/** Общие типы оболочки и режимов. */

import type { ComponentType } from "react";
import type { Me } from "../api";
import type { IconName } from "../ui/Icon";

export type ModeKey = "city" | "trips" | "procurements" | "intake" | "site" | "requests" | "si-templates" | "att-templates";
export type SectionKey =
  | Exclude<ModeKey, "site">
  | "today" | "control" | "journal" | "availability" | "tasks" | "assistant" | "si-types" | "staff";

export type GoOptions = {
  /** Открыть командировку в «Командировках». */
  trip?: number;
  /** Сразу после перехода попросить режим сделать действие (см. ui/modeActions.ts). */
  action?: string;
  payload?: unknown;
  /** Хвост адреса: #/si-templates/12 → sub "12". */
  sub?: string;
};
export type Go = (key: SectionKey | string, options?: GoOptions) => void;

/** Что получает каждый режим от оболочки. */
export type ModeProps = {
  me: Me | null;
  isManager: boolean;
  isControl: boolean;
  /** Владелец, руководитель, метролог — проверяют типы СИ и шаблоны. */
  isReviewer: boolean;
  go: Go;
  /** Пересчитать счётчики в боковой панели (новые заявки, типы на проверке). */
  refreshCounts: () => void;
};

export type Access = { role?: string; isManager: boolean; isControl: boolean; isReviewer: boolean };

export type PrimaryAction = {
  label: string;
  icon: IconName;
  /** Имя действия для emitModeAction(key, action). */
  action: string;
  visible?: (access: Access) => boolean;
};

export type SectionDef = {
  key: SectionKey | "site";
  title: string;
  /** Короче — для таб-бара телефона и свёрнутой панели. */
  short: string;
  icon: IconName;
  /** Цифра-клавиша режима 1…8. */
  digit?: number;
  group: "home" | "modes" | "work" | "reference";
  /** Одна строка: что здесь делают (подзаголовок и плитка). */
  description: string;
  visible?: (access: Access) => boolean;
  /** Компонент режима (обычно lazy). У «Сайта» его нет — только href. */
  component?: ComponentType<ModeProps>;
  href?: string;
  /** Во всю высоту окна, без отступов main. */
  flush?: boolean;
  /** Главное действие режима: клавиша N, большая кнопка на телефоне, палитра. */
  primary?: PrimaryAction;
  /** Синонимы для палитры команд. */
  keywords?: string;
};
