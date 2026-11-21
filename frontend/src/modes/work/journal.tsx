import JournalTab from "../../components/JournalTab";
import type { ModeProps } from "../types";

export default function JournalSection({ isManager, go }: ModeProps) {
  return <JournalTab isManager={isManager} onOpenTypes={() => go("si-types")} />;
}
