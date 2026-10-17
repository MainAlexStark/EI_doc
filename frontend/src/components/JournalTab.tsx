import { useState } from "react";
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
        <div className="segmented" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={view === "protocols"}
            className={view === "protocols" ? "active" : ""}
            onClick={() => setView("protocols")}
          >
            Протоколы
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "arshin"}
            className={view === "arshin" ? "active" : ""}
            onClick={() => setView("arshin")}
          >
            Нумерация и Аршин
          </button>
        </div>
      </div>
      {view === "protocols" ? <Journal /> : <Normocontrol onOpenTypes={onOpenTypes} />}
    </>
  );
}
