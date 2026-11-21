/** Режим 7 «Шаблоны поверки СИ» — пока TemplateStudio (чат ассистента рядом с шаблоном).
 *  Адрес #/si-templates/<id типа СИ> открывает тип сразу. */

import TemplateStudio from "../../assistant/TemplateStudio";
import type { ModeProps } from "../types";

export default function SiTemplatesMode(_props: ModeProps) {
  return <TemplateStudio />;
}
