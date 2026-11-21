/** Режим 6 «Заявки» — пока старый RequestsInbox.
 *  Пересборка: заменить содержимое этого файла (пропсы — ModeProps). */

import RequestsInbox from "../../components/RequestsInbox";
import type { ModeProps } from "../types";

export default function RequestsMode({ refreshCounts }: ModeProps) {
  return <RequestsInbox onChanged={refreshCounts} />;
}
