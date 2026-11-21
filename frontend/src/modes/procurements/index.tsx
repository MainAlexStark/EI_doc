/** Режим 3 «Закупки» — пока раздел «Закупки» старого экрана «Выезды» (список → ProcurementView).
 *  Пересборка: заменить содержимое этого файла (пропсы — ModeProps). */

import Trips from "../../components/Trips";
import type { ModeProps } from "../types";

export default function ProcurementsMode({ me, isManager }: ModeProps) {
  return <Trips me={me} isManager={isManager} mode="procurements" only="procurements" />;
}
