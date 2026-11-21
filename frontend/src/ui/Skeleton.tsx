/** Скелетоны — вместо спиннера там, где известна форма данных. */

import type { CSSProperties } from "react";

export function Skeleton({
  width, height = 14, radius, className, style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`skeleton ${className ?? ""}`}
      aria-hidden="true"
      style={{ width: width ?? "100%", height, borderRadius: radius, ...style }}
    />
  );
}

/** Несколько строк текста; последняя короче. */
export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <span className={`skeleton-text ${className ?? ""}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} width={index === lines - 1 && lines > 1 ? "62%" : "100%"} />
      ))}
    </span>
  );
}

/** Список строк (как card-list): иконка-плашка + две строки текста. */
export function SkeletonList({ rows = 4, label = "Загружаю…" }: { rows?: number; label?: string }) {
  return (
    <div className="skeleton-list" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton-row" key={index}>
          <Skeleton width={36} height={36} radius={10} />
          <span className="grow">
            <Skeleton width={`${70 - (index % 3) * 12}%`} height={13} />
            <Skeleton width={`${45 - (index % 2) * 10}%`} height={11} style={{ marginTop: 7 }} />
          </span>
        </div>
      ))}
    </div>
  );
}

export default Skeleton;
