/** Режим 3 «Закупки» — контракты 44/223-ФЗ: список → карточка (выжимка, документы, пропуска, заходы).
 *
 * Адрес карточки — `#/procurements/<id>`. Действия режима: `new`, `new-procurement` (руководство),
 * `open-procurement` (id), `list`. Поверитель видит закупки своих командировок только на чтение
 * (права считает сервер: visible_procurements, can_edit). */

import { useState } from "react";
import { ProcurementForm } from "../../components/Procurements";
import toast from "../../ui/Toast";
import { useModeAction } from "../../ui/modeActions";
import type { ModeProps } from "../types";
import ProcurementDetail from "./Detail";
import ProcurementList from "./List";
import { useHashId } from "./hash";
import "./procurements.css";

export default function ProcurementsMode({ isManager, go }: ModeProps) {
  const [openId, setOpenId] = useHashId("procurements");
  const [creating, setCreating] = useState(false);
  const [version, setVersion] = useState(0);

  const create = isManager ? () => setCreating(true) : undefined;
  useModeAction("procurements", {
    new: create,
    "new-procurement": create,
    "open-procurement": (id) => typeof id === "number" && setOpenId(id),
    list: () => setOpenId(null),
  });

  return (
    <div className="pr-mode">
      {openId != null ? (
        <ProcurementDetail key={openId} id={openId} go={go} onBack={() => { setOpenId(null); setVersion((v) => v + 1); }} />
      ) : (
        <ProcurementList canCreate={isManager} onOpen={setOpenId} onCreate={() => setCreating(true)} version={version} />
      )}
      {creating && (
        <ProcurementForm
          onClose={() => setCreating(false)}
          onSaved={(item) => {
            setCreating(false);
            setVersion((v) => v + 1);
            setOpenId(item.id);
            toast.success(`Закупка ${item.label} заведена`, { description: "Загрузите документы — ассистент соберёт выжимку" });
          }}
        />
      )}
    </div>
  );
}
