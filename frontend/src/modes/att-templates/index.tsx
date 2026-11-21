/** Режим 8 «Шаблоны аттестации» — пока старый экран «Аттестация ИО».
 *  Пересборка: заменить содержимое этого файла (пропсы — ModeProps). */

import Attestations from "../../components/Attestations";
import type { ModeProps } from "../types";

export default function AttTemplatesMode({ me }: ModeProps) {
  return <Attestations me={me} />;
}
