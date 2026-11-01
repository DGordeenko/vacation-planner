import { useEffect, useState } from "react";
import { tagsApi } from "../api";
import type { EmployeeBrief } from "../types";

/** Выбор сотрудника поиском по ФИО, табельному или почте.
    Кого показать, решает сервер (своя структура или все — у администратора). */
export default function EmployeePicker({ label, selected, onSelect, exclude, autoFocus }: {
  label: string;
  /** Выбранный сотрудник показывается плашкой с крестиком; null — поле поиска. */
  selected?: EmployeeBrief | null;
  onSelect: (employee: EmployeeBrief | null) => void;
  /** Кого не предлагать (уже назначенных). */
  exclude?: Set<string>;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<EmployeeBrief[]>([]);
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    setSearched(false);
    if (query.trim().length < 2) { setFound([]); return; }
    const timer = window.setTimeout(() => {
      tagsApi.searchEmployees(query.trim())
        .then((rows) => { setFound(rows); setSearched(true); })
        .catch(() => setFound([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const options = found.filter((e) => !exclude?.has(e.id)).slice(0, 8);

  function pick(employee: EmployeeBrief) {
    onSelect(employee);
    setQuery(""); setFound([]);
  }

  return (
    <div className="vs-field vs-picker">
      {label}
      {selected ? (
        <span className="vs-chip-picked">
          {selected.full_name}
          <button className="vs-link" aria-label={`Убрать: ${selected.full_name}`}
            onClick={() => onSelect(null)}>✕</button>
        </span>
      ) : (
        <>
          <input value={query} placeholder="ФИО, табельный или почта" autoFocus={autoFocus}
            role="combobox" aria-expanded={options.length > 0} aria-autocomplete="list"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && options.length > 0) { e.preventDefault(); pick(options[0]); }
              if (e.key === "Escape") { setQuery(""); setFound([]); }
            }} />
          {options.length > 0 && (
            <ul className="vs-list vs-found vs-picker-list" role="listbox">
              {options.map((e) => (
                <li key={e.id} role="option" aria-selected={false}>
                  <button className="vs-item vs-found-item" onClick={() => pick(e)}>
                    <span>{e.full_name}</span>
                    <span className="vs-muted vs-small">
                      {e.employee_number}{e.org_unit ? ` · ${e.org_unit}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {searched && options.length === 0 && (
            <span className="vs-muted vs-small">Никого не нашлось</span>
          )}
        </>
      )}
    </div>
  );
}
