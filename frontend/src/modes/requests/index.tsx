/** Режим 6 «Заявки» — разбор входящих потоком (modes/requests/Inbox.tsx).
 *  Действие режима `new` — запись по телефону (та же «Запись», что в «Городе»). */

import { useState } from "react";
import VisitModal from "../../components/Visit";
import toast from "../../ui/Toast";
import { useModeAction } from "../../ui/modeActions";
import type { ModeProps } from "../types";
import Inbox from "./Inbox";

export default function RequestsMode({ me, go, refreshCounts }: ModeProps) {
  const [creating, setCreating] = useState(false);
  const [version, setVersion] = useState(0);
  useModeAction("requests", { new: () => setCreating(true) });

  return (
    <>
      <Inbox key={version} onChanged={refreshCounts} go={go} onNew={me ? () => setCreating(true) : undefined} />
      {creating && me && (
        <VisitModal
          me={me}
          onClose={() => setCreating(false)}
          onCreated={(result) => {
            setCreating(false);
            setVersion((v) => v + 1);
            refreshCounts();
            toast.success(`Записано: заявка №${result.request_id}`, {
              description: `${result.employee} · ${result.trip_title}`,
              action: { label: "К выезду", onClick: () => go("city", { action: "open-trip", payload: result.trip_id }) },
            });
          }}
        />
      )}
    </>
  );
}
