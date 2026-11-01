/** Тексты интерфейса: склонение по числу и разбор ошибок сервера. */

import { ApiError } from "./api";

/** plural(3, "день", "дня", "дней") → «дня». */
export function plural(n: number, one: string, few: string, many: string) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Ошибки по полям из ответа сервера: и вида {errors: {...}}, и DRF {field: [...]}. */
export function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError)) return {};
  const body = (err.payload ?? {}) as Record<string, unknown>;
  const source = (body.errors && typeof body.errors === "object" && !Array.isArray(body.errors)
    ? body.errors : body) as Record<string, unknown>;
  const result: Record<string, string> = {};
  Object.entries(source).forEach(([key, value]) => {
    if (Array.isArray(value)) result[key] = value.join(" ");
    else if (typeof value === "string" && key !== "detail" && key !== "code") result[key] = value;
  });
  return result;
}

/** Одна строка для пользователя: ошибки полей, иначе сообщение ответа. */
export function errorText(err: unknown, fallback = "Не удалось выполнить действие") {
  const fields = Object.values(fieldErrors(err));
  if (fields.length) return fields.join(" ");
  return err instanceof Error && err.message ? err.message : fallback;
}
