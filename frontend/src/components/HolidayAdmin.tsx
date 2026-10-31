import { useCallback, useEffect, useMemo, useState } from "react";
import { adminApi } from "../api";
import { buildMonth, fmt, iso, isWeekend, MONTHS, WEEKDAYS } from "../dates";
import HolidayImport from "./HolidayImport";
import type { CalendarImpact, DayType, HolidayRow, YearSummary } from "../types";

const TYPE_LABELS: Record<DayType, string> = {
  holiday: "Праздничный нерабочий",
  pre_holiday_short: "Сокращённый предпраздничный",
  transferred_workday: "Перенесённый рабочий",
};

const TYPE_SHORT: Record<DayType, string> = {
  holiday: "праздник",
  pre_holiday_short: "сокращённый",
  transferred_workday: "рабочий",
};

export default function HolidayAdmin({ onBack }: { onBack: () => void }) {
  const nextYear = new Date().getFullYear() + 1;
  const [year, setYear] = useState(nextYear);
  const [years, setYears] = useState<YearSummary[]>([]);
  const [rows, setRows] = useState<HolidayRow[]>([]);
  const [impact, setImpact] = useState<CalendarImpact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draftDate, setDraftDate] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftType, setDraftType] = useState<DayType>("holiday");
  const [showImport, setShowImport] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [list, summary, effect] = await Promise.all([
        adminApi.list(year), adminApi.years(), adminApi.impact(year),
      ]);
      setRows(list.results);
      setYears(summary);
      setImpact(effect);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить календарь");
    } finally {
      setLoading(false);
    }
  }, [year]);

  useEffect(() => { void reload(); }, [reload]);

  const byDate = useMemo(
    () => new Map(rows.map((row) => [row.date, row])), [rows]
  );

  async function addDay() {
    if (!draftDate || !draftName.trim()) return;
    try {
      await adminApi.create(draftDate, draftName.trim(), draftType);
      setDraftDate(null);
      setDraftName("");
      setDraftType("holiday");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось добавить день");
    }
  }

  async function removeDay(id: string) {
    try {
      await adminApi.remove(id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить день");
    }
  }

  async function changeType(row: HolidayRow, day_type: DayType) {
    try {
      await adminApi.update(row.id, { day_type });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить тип");
    }
  }

  async function clearYear() {
    try {
      await adminApi.clearYear(year);
      setConfirmClear(false);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось очистить календарь");
    }
  }

  async function copyFrom(source: number) {
    try {
      const result = await adminApi.copyYear(source, year);
      if (result.skipped.length) {
        setError(`Перенесено ${result.created} дней. Пропущено (нет такой даты `
          + `в ${year} году): ${result.skipped.join(", ")}`);
      }
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось перенести календарь");
    }
  }

  const donors = years.filter((row) => row.year !== year && row.count > 0);
  const counts = {
    holiday: rows.filter((r) => r.day_type === "holiday").length,
    short: rows.filter((r) => r.day_type === "pre_holiday_short").length,
    workday: rows.filter((r) => r.day_type === "transferred_workday").length,
  };

  function dayClick(key: string) {
    const existing = byDate.get(key);
    if (existing) {
      setDraftDate(null);
      return;
    }
    setDraftDate(key);
    setDraftName("");
    setDraftType("holiday");
  }

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Календарь праздников</h1>
      </div>

      {error && (
        <div className="vs-error vs-error-inline">
          {error}
          <button className="vs-link" onClick={() => setError(null)}>скрыть</button>
        </div>
      )}

      <div className="vs-admin-shell">
        <aside className="vs-rail">
          <div className="vs-card">
            <h2>Год</h2>
            <div className="vs-years">
              {[nextYear - 1, nextYear, nextYear + 1].map((option) => (
                <button key={option} className="vs-tag" aria-pressed={year === option}
                  onClick={() => setYear(option)}>
                  {option}
                  <span className="vs-tag-limit">
                    {years.find((y) => y.year === option)?.count ?? 0}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="vs-card">
            <h2>Состав календаря</h2>
            <dl className="vs-rows">
              <div className="vs-row"><dt>Праздничных</dt><dd>{counts.holiday}</dd></div>
              <div className="vs-row"><dt>Сокращённых</dt><dd>{counts.short}</dd></div>
              <div className="vs-row"><dt>Перенесённых рабочих</dt><dd>{counts.workday}</dd></div>
            </dl>
            {rows.length === 0 && !loading && (
              <p className="vs-need vs-small">
                Календарь на {year} год пуст. Сотрудники не смогут планировать отпуск
                корректно: праздники не будут вычитаться из отпускных дней.
              </p>
            )}
          </div>

          <div className="vs-card">
            <h2>Действия</h2>
            <div className="vs-actions">
              <button className="vs-btn block" onClick={() => setShowImport(true)}>
                Загрузить из CSV
              </button>
              {donors.map((donor) => (
                <button key={donor.year} className="vs-btn block"
                  onClick={() => copyFrom(donor.year)} disabled={rows.length > 0}>
                  Перенести из {donor.year}
                </button>
              ))}
              {rows.length > 0 && (
                <button className="vs-btn danger block" onClick={() => setConfirmClear(true)}>
                  Очистить год
                </button>
              )}
            </div>
            {donors.length > 0 && rows.length > 0 && (
              <p className="vs-muted vs-small">
                Перенос доступен только для пустого года.
              </p>
            )}
          </div>

          {impact && impact.affected_entries > 0 && (
            <div className="vs-card">
              <h2>Последствия правок</h2>
              <p className="vs-small">
                На {year} год уже внесено {impact.affected_entries} отпусков
                у {impact.affected_employees} сотрудников. Изменение календаря
                <strong> не пересчитает их автоматически</strong> — потребуется
                уведомить людей и пересохранить затронутые отпуска.
              </p>
            </div>
          )}
        </aside>

        <main>
          <p className="vs-muted vs-small vs-hint">
            Нажмите на день, чтобы добавить его в календарь. Нажатие на отмеченный
            день снимает выделение формы.
          </p>

          <div className="vs-grid">
            {MONTHS.map((label, monthIndex) => (
              <section className="vs-month" key={label}>
                <h3>{label}</h3>
                <div className="vs-week">
                  {WEEKDAYS.map((day) => <div className="vs-wd" key={day}>{day}</div>)}
                  {buildMonth(year, monthIndex).map((cell, index) => {
                    if (!cell) return <div className="vs-day void" key={`v${index}`} />;
                    const key = iso(cell);
                    const row = byDate.get(key);
                    const classes = ["vs-day"];
                    if (isWeekend(cell)) classes.push("weekend");
                    if (row) classes.push(`mark-${row.day_type}`);
                    if (draftDate === key) classes.push("marking");
                    return (
                      <button key={key} className={classes.join(" ")}
                        onClick={() => dayClick(key)}
                        title={row ? `${row.name} — ${TYPE_LABELS[row.day_type]}` : key}>
                        {cell.getUTCDate()}
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>

          <div className="vs-card vs-legend-card">
            <span><i className="vs-chip mark-holiday" /> Праздничный нерабочий</span>
            <span><i className="vs-chip mark-pre_holiday_short" /> Сокращённый</span>
            <span><i className="vs-chip mark-transferred_workday" /> Перенесённый рабочий</span>
          </div>

          {rows.length > 0 && (
            <div className="vs-card">
              <h2>Дни календаря {year} года</h2>
              <div className="vs-table-scroll"><table className="vs-table">
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td className="vs-td-date">{fmt(row.date)}</td>
                      <td>{row.name}</td>
                      <td>
                        <select className="vs-select" value={row.day_type}
                          onChange={(e) => changeType(row, e.target.value as DayType)}>
                          {(Object.keys(TYPE_LABELS) as DayType[]).map((type) => (
                            <option key={type} value={type}>{TYPE_SHORT[type]}</option>
                          ))}
                        </select>
                      </td>
                      <td className="vs-td-action">
                        <button className="vs-x" onClick={() => removeDay(row.id)}
                          aria-label={`Удалить ${row.name}`}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </div>
          )}
        </main>
      </div>

      {draftDate && (
        <div className="vs-panel" role="dialog" aria-label="Новый день календаря">
          <div className="vs-panel-head">
            <strong>Добавить {fmt(draftDate)}</strong>
          </div>
          <div className="vs-panel-body">
            <label className="vs-field">
              Название
              <input type="text" value={draftName} autoFocus
                placeholder="Например, День России"
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addDay()} />
            </label>
            <label className="vs-field">
              Тип дня
              <select className="vs-select" value={draftType}
                onChange={(e) => setDraftType(e.target.value as DayType)}>
                {(Object.keys(TYPE_LABELS) as DayType[]).map((type) => (
                  <option key={type} value={type}>{TYPE_LABELS[type]}</option>
                ))}
              </select>
            </label>
            <p className="vs-muted vs-small">
              Праздничные дни не списываются с баланса отпуска и продлевают его.
              Перенесённый рабочий день считается обычным рабочим.
            </p>
          </div>
          <div className="vs-panel-foot">
            <button className="vs-btn" onClick={() => setDraftDate(null)}>Отмена</button>
            <button className="vs-btn primary" onClick={addDay} disabled={!draftName.trim()}>
              Добавить
            </button>
          </div>
        </div>
      )}

      {confirmClear && (
        <div className="vs-panel" role="dialog" aria-label="Очистка календаря">
          <div className="vs-panel-head"><strong>Очистить календарь {year} года?</strong></div>
          <div className="vs-panel-body">
            <p className="vs-small">
              Будут удалены все {rows.length} дней.
              {impact && impact.affected_entries > 0 && (
                <> Это затронет расчёт {impact.affected_entries} уже внесённых
                отпусков — они не пересчитаются автоматически.</>
              )}
            </p>
          </div>
          <div className="vs-panel-foot">
            <button className="vs-btn" onClick={() => setConfirmClear(false)}>Отмена</button>
            <button className="vs-btn danger" onClick={clearYear}>Очистить</button>
          </div>
        </div>
      )}

      {showImport && (
        <HolidayImport year={year} onDone={reload} onClose={() => setShowImport(false)} />
      )}
    </div>
  );
}
