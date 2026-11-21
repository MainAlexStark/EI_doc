import Tasks from "../../components/Tasks";
import type { ModeProps } from "../types";

export default function TasksSection({ me }: ModeProps) {
  return <Tasks me={me} />;
}
