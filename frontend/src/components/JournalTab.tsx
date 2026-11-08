import { useState } from "react";
import { Segmented } from "../ui/controls";
import Journal from "./Journal";
import Normocontrol from "./Normocontrol";

type View = "protocols" | "arshin";

/** Одна вкладка «Журнал» вместо двух (решение 17.10.2026).
 * Поверитель видит только журнал. Менеджеру сверху переключатель:
 * протоколы — или нумерация, подпись и отправка в Аршин (бывший нормоконтроль). */
export default function JournalTab({ isManager, onOpenTypes }: { isManager: boolean; onOpenTypes: () => void }) {
  const [view, setView] = useState<View>("protocols");

  if (!isManager) return <Journal />;

  return (
    <>
      <div className="toolbar">
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { value: "protocols", label: "Протоколы", icon: "doc" },
            { value: "arshin", label: "Нумерация и Аршин", icon: "layers" },
          ]}
        />
      </div>
      {view === "protocols" ? <Journal /> : <Normocontrol onOpenTypes={onOpenTypes} />}
    </>
  );
}
