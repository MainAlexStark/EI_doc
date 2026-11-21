import Staff from "../../components/control/Staff";
import type { ModeProps } from "../types";

export default function StaffSection({ me }: ModeProps) {
  return <Staff meId={me?.employee?.id} />;
}
