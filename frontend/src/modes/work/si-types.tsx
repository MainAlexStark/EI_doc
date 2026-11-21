import SiTypes from "../../components/SiTypes";
import type { ModeProps } from "../types";

export default function SiTypesSection({ me, refreshCounts }: ModeProps) {
  return <SiTypes me={me} onChanged={refreshCounts} />;
}
