/** Открытая карточка режима — в хвосте адреса: `#/procurements/12`, `#/intake/345`.
 *
 * Адрес ставим через `location.hash`, чтобы оболочка (App.tsx, hashchange) знала хвост и клик
 * по режиму в боковой панели возвращал к списку. Назад в браузере — тоже к списку. */

import { useCallback, useEffect, useState } from "react";

export function readHashId(key: string): number | null {
  const match = window.location.hash.match(new RegExp(`^#/${key}/(\\d+)`));
  return match ? Number(match[1]) : null;
}

export function useHashId(key: string): [number | null, (id: number | null) => void] {
  const [id, setId] = useState<number | null>(() => readHashId(key));

  useEffect(() => {
    const sync = () => {
      if (window.location.hash.startsWith(`#/${key}`)) setId(readHashId(key));
    };
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, [key]);

  // Оболочка сменила адрес через pushState (клик по режиму) — событий нет, сверяемся после рендера.
  useEffect(() => {
    if (!window.location.hash.startsWith(`#/${key}`)) return;
    const current = readHashId(key);
    if (current !== id) setId(current);
  });

  const open = useCallback(
    (next: number | null) => {
      const hash = next ? `#/${key}/${next}` : `#/${key}`;
      if (window.location.hash !== hash) window.location.hash = hash;
      setId(next);
      window.scrollTo({ top: 0 });
    },
    [key],
  );
  return [id, open];
}
