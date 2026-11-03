import { MONTHS } from "../dates";
import type { SettingHistoryRow } from "../types";

/** Текущий месяц в виде «ГГГГ-ММ» — по умолчанию изменение действует с него. */
export function thisMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

const MONTHS_GENITIVE = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
  "августа", "сентября", "октября", "ноября", "декабря"];

/** «2027-01-01» → «января 2027» — для «с января 2027». */
function monthLabel(iso: string) {
  const [year, month] = iso.split("-").map(Number);
  return `${MONTHS_GENITIVE[month - 1]} ${year}`;
}

/** Выбор месяца двумя списками: поле type="month" Safari не поддерживает. */
export function MonthPicker({ label, value, onChange }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [year, month] = value.split("-");
  const now = new Date().getFullYear();
  const years = [now - 2, now - 1, now, now + 1, now + 2];
  return (
    <fieldset className="vs-field vs-month-picker">
      <legend>{label}</legend>
      <select value={month} aria-label="Месяц"
        onChange={(e) => onChange(`${year}-${e.target.value}`)}>
        {MONTHS.map((name, i) => (
          <option key={name} value={String(i + 1).padStart(2, "0")}>{name}</option>
        ))}
      </select>
      <select value={year} aria-label="Год" onChange={(e) => onChange(`${e.target.value}-${month}`)}>
        {years.map((y) => <option key={y} value={y}>{y}</option>)}
      </select>
    </fieldset>
  );
}

/** История «с месяца». Начальное значение подписано «ранее»; его не удалить. */
export function HistoryLines({ rows, format, onRemove }: {
  rows: SettingHistoryRow[];
  format: (value: number | null) => string;
  onRemove?: (effectiveFrom: string) => void;
}) {
  if (rows.length < 2) return null;
  return (
    <ul className="vs-history">
      {rows.map((row, i) => (
        <li key={row.effective_from}>
          <span className="vs-muted">{i === 0 ? "ранее" : `с ${monthLabel(row.effective_from)}`}</span>
          {" — "}{format(row.value)}
          {i > 0 && onRemove && (
            <button className="vs-x" onClick={() => onRemove(row.effective_from)}
              aria-label={`Отменить изменение с ${monthLabel(row.effective_from)}`}>✕</button>
          )}
        </li>
      ))}
    </ul>
  );
}
