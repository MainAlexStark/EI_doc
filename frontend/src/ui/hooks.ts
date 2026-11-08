import { useEffect, useState } from "react";

/** Совпадает ли медиазапрос — например, «телефон» (max-width: 760px). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

export const PHONE = "(max-width: 760px)";

/** Значение в localStorage — для мелочей вида «последний выбранный вид календаря». */
export function usePersistent<T extends string>(key: string, initial: T, allowed?: readonly T[]): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key) as T | null;
      if (stored && (!allowed || allowed.includes(stored))) return stored;
    } catch {
      /* приватный режим */
    }
    return initial;
  });
  const set = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, next);
    } catch {
      /* ок */
    }
  };
  return [value, set];
}
