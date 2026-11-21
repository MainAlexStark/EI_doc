/** Режим 4 «Приёмки» — пока старый экран FieldWork (заказы, привезённые в лабораторию).
 *  Пересборка: заменить содержимое этого файла (пропсы — ModeProps). */

import FieldWork from "../../components/FieldWork";
import type { ModeProps } from "../types";

export default function IntakeMode({ me, isManager }: ModeProps) {
  return <FieldWork me={me} isManager={isManager} />;
}
