/** «График»: руководству — команда или свой, поверителю — свой. */

import Availability from "../../components/Availability";
import TeamSchedule from "../../components/control/TeamSchedule";
import { PageActions, Segmented } from "../../ui/controls";
import { usePersistent } from "../../ui/hooks";
import type { ModeProps } from "../types";

export default function AvailabilitySection({ me, isControl }: ModeProps) {
  const [graphMode, setGraphMode] = usePersistent<"team" | "mine">("ei_doc_graph_mode", "team", ["team", "mine"]);
  if (!isControl) return <Availability me={me} />;
  return (
    <>
      <PageActions>
        <Segmented
          size="sm"
          value={graphMode}
          onChange={setGraphMode}
          options={[{ value: "team", label: "Команда", icon: "users" }, { value: "mine", label: "Мой", icon: "user" }]}
        />
      </PageActions>
      {graphMode === "team" ? <div className="flush-pad"><TeamSchedule /></div> : <Availability me={me} />}
    </>
  );
}
