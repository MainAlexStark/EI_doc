import ControlPanel from "../../components/control/ControlPanel";
import type { ModeProps } from "../types";

export default function ControlSection({ go }: ModeProps) {
  return <ControlPanel onGo={(tab, trip) => go(tab, trip ? { trip } : undefined)} />;
}
