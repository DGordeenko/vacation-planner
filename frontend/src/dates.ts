/** Работа с датами календаря. Расчёты отпускных дней — на сервере. */

export const MONTHS = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];
export const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

export const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Сегодня по местному времени. iso(new Date()) дал бы дату по UTC:
    в Москве с полуночи до трёх ночи это ещё вчера. */
export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** День недели словом для даты ГГГГ-ММ-ДД, без сдвига часового пояса. */
export function weekdayName(isoDate: string) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("ru-RU", { weekday: "short", timeZone: "UTC" });
}
export const isWeekend = (d: Date) => [0, 6].includes(d.getUTCDay());

export function buildMonth(year: number, monthIndex: number): (Date | null)[] {
  const first = new Date(Date.UTC(year, monthIndex, 1));
  const lead = (first.getUTCDay() + 6) % 7;
  const total = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
  const cells: (Date | null)[] = Array(lead).fill(null);
  for (let day = 1; day <= total; day += 1) {
    cells.push(new Date(Date.UTC(year, monthIndex, day)));
  }
  return cells;
}

export function fmt(isoDate: string) {
  const [, month, day] = isoDate.split("-");
  return `${day}.${month}`;
}

export function inRange(key: string, from: string, to: string) {
  return key >= from && key <= to;
}
