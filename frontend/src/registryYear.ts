/**
 * Какие типы СИ подходят прибору по году выпуска — то же правило, что на сервере
 * (backend/apps/catalog/registry_year.py), для поиска без связи по локальной копии справочника.
 *
 * Номер в Госреестре — «16078-13»: номер типа и год внесения в реестр. Для счётчика 2020 года
 * 16078-22 ещё не существовал, а 16078-05 уже сменён записью 16078-13. Выпущенному в год смены
 * (2013) подходят обе записи.
 */
const MIN_YEAR = 1950;

/** Год выпуска из поля ввода: четыре цифры в разумных пределах, иначе — «год не указан». */
export function parseYear(raw: string | number | null | undefined): number | null {
  const text = String(raw ?? "").trim();
  if (!/^\d{4}$/.test(text)) return null;
  const year = Number(text);
  return year >= MIN_YEAR && year <= new Date().getFullYear() + 1 ? year : null;
}

function splitNumber(registryNumber: string): { base: string; year: number } | null {
  const match = /^\s*(\d+)\s*-\s*(\d{2})\s*$/.exec(registryNumber ?? "");
  if (!match) return null;
  const short = Number(match[2]);
  const century = short <= new Date().getFullYear() % 100 ? 2000 : 1900;
  return { base: match[1].replace(/^0+/, "") || "0", year: century + short };
}

export function fitsYear<T extends { registry_number: string }>(items: T[], year: number | null): T[] {
  if (!year) return items;
  const parsed = items.map((item) => splitNumber(item.registry_number));
  // Годы записей каждого номера, уже бывших в реестре к году выпуска, по убыванию.
  const known = new Map<string, number[]>();
  for (const number of parsed) {
    if (!number || number.year > year) continue;
    known.set(number.base, [...(known.get(number.base) ?? []), number.year]);
  }
  for (const years of known.values()) years.sort((a, b) => b - a);

  return items.filter((_, index) => {
    const number = parsed[index];
    if (!number) return true; // номер не разобрать — судить не по чему
    if (number.year > year) return false;
    const [current, ...older] = known.get(number.base)!;
    const previous = older.find((value) => value < current);
    return number.year === current || (current === year && number.year === previous);
  });
}
