/** Режим 1 «Город» — пока старый экран «Выезды» (карта, день поверителя, командировки).
 *  Пересборка: заменить содержимое этого файла (пропсы — ModeProps). */

import Trips from "../../components/Trips";
import type { ModeProps } from "../types";

export default function CityMode({ me, isManager }: ModeProps) {
  return <Trips me={me} isManager={isManager} mode="city" />;
}
