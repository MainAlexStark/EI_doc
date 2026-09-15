/**
 * Маска ввода российского номера: +7 (900) 123-45-67.
 *
 * Формат ровно тот, что проверяет PHONE_RE на бэкенде (apps/hub/api.py) —
 * одна и та же строка должна проходить оба регэкспа.
 */

/** Разбирает произвольный ввод в маску по мере набора (не требует, чтобы номер был введён полностью). */
export function formatPhoneInput(raw: string): string {
  let digits = raw.replace(/\D/g, "");
  if (!digits) return "";

  // "8 900 ..." и "9 900 ..." — оба обычных способа начать ввод в России,
  // приводим к единому +7.
  if (digits[0] === "8") digits = `7${digits.slice(1)}`;
  else if (digits[0] !== "7") digits = `7${digits}`;
  digits = digits.slice(0, 11);

  const rest = digits.slice(1); // до 10 цифр после 7
  if (rest.length === 0) return "+7";

  let out = `+7 (${rest.slice(0, 3)}`;
  if (rest.length >= 3) out += ")";
  if (rest.length > 3) out += ` ${rest.slice(3, 6)}`;
  if (rest.length > 6) out += `-${rest.slice(6, 8)}`;
  if (rest.length > 8) out += `-${rest.slice(8, 10)}`;
  return out;
}

const PHONE_RE = /^\+7 \(\d{3}\) \d{3}-\d{2}-\d{2}$/;

/** Введён ли номер целиком — тот же формат, что проверяет сервер. */
export function isPhoneComplete(value: string): boolean {
  return PHONE_RE.test(value);
}
