/** Режим 2 «Командировки» — пока старый экран «Выезды» без карты и закупок.
 *  Пересборка: заменить содержимое этого файла (пропсы — ModeProps). */

import Trips from "../../components/Trips";
import type { ModeProps } from "../types";

export default function TripsMode({ me, isManager }: ModeProps) {
  return <Trips me={me} isManager={isManager} mode="trips" only="trips" />;
}
