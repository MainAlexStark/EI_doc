import { daysUntil, ruShort } from "./tripUtil";

/** Срок исполнения контракта: спокойно, пока далеко; жёлтым за две недели; красным после. */
export function DeadlinePill({ deadline, closed }: { deadline: string | null; closed?: boolean }) {
  const left = daysUntil(deadline);
  if (left == null || closed) return null;
  if (left < 0) return <span className="pill bad">срок истёк {ruShort(deadline)}</span>;
  if (left <= 14) return <span className="pill warn">до срока {left} дн.</span>;
  return <span className="pill muted plain">до {ruShort(deadline)}</span>;
}
