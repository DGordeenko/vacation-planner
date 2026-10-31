import { useCallback, useEffect, useState } from "react";
import { api, ApiError, rvdApi, tagsApi, type RvdInput } from "../api";
import { fmt, MONTHS, weekdayName } from "../dates";
import type { EmployeeBrief, Holiday, RvdCompensation, RvdRow } from "../types";

function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError)) return {};
  const body = (err.payload ?? {}) as Record<string, unknown>;
  const result: Record<string, string> = {};
  Object.entries(body).forEach(([key, value]) => {
    if (Array.isArray(value)) result[key] = value.join(" ");
  });
  return result;
}

/** Прошедший месяц: по принятому процессу РВД вносят в начале месяца за предыдущий. */
function previousMonth() {
  const now = new Date();
  const month = now.getMonth() === 0 ? 11 : now.getMonth() - 1;
  return { year: now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear(), month };
}

/** Выходной или праздник по производственному календарю: перенесённый
    рабочий день — рабочий, праздник в будни — нерабочий. */
function isRestDay(isoDate: string, holidays: Holiday[]) {
  const special = holidays.find((h) => h.date === isoDate);
  if (special) return special.day_type === "holiday";
  const [y, m, d] = isoDate.split("-").map(Number);
  return [0, 6].includes(new Date(Date.UTC(y, m - 1, d)).getUTCDay());
}

/** Ввод работы в выходные дни (релиз 1.2). Запись администратора и есть
    подтверждение: отдельного согласования РВД нет. Часы отгула начисляет
    сервер, если выбрана компенсация «оплата + отгул». */
export default function RvdAdmin({ onBack }: { onBack: () => void }) {
  const [period, setPeriod] = useState(previousMonth);
  const [rows, setRows] = useState<RvdRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRows(null);
    try {
      setRows((await rvdApi.month(period.year, period.month + 1)).results);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить");
    }
  }, [period]);

  useEffect(() => { void load(); }, [load]);

  function shift(delta: number) {
    setPeriod(({ year, month }) => {
      const index = year * 12 + month + delta;
      return { year: Math.floor(index / 12), month: index % 12 };
    });
  }

  async function remove(row: RvdRow) {
    if (!window.confirm(`Удалить РВД ${row.employee_name} за ${fmt(row.date_from)}? `
      + "Начисленные за неё часы отгула тоже будут сняты.")) return;
    try {
      await rvdApi.remove(row.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    }
  }

  const totalHours = (rows ?? []).reduce((sum, r) => sum + Number(r.work_hours), 0);

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Работа в выходные</h1>
      </div>
      <p className="vs-muted vs-small vs-team-note">
        В начале месяца внесите работу в выходные и праздники за прошедший месяц.
        Запись администратора и есть подтверждение. При компенсации «оплата + отгул»
        сотруднику начисляются часы, и он сможет попросить отгул.
      </p>

      <RvdForm onSaved={(year, month) => {
        // Показываем месяц только что внесённой записи — иначе она «пропадает».
        if (year === period.year && month === period.month) void load();
        else setPeriod({ year, month });
      }} />

      <div className="vs-team-controls">
        <div className="vs-month-switch">
          <button className="vs-btn" onClick={() => shift(-1)} aria-label="Предыдущий месяц">←</button>
          <strong>{MONTHS[period.month]} {period.year}</strong>
          <button className="vs-btn" onClick={() => shift(1)} aria-label="Следующий месяц">→</button>
        </div>
        {rows && rows.length > 0 && (
          <span className="vs-muted vs-small">
            Записей: {rows.length}, часов: {totalHours.toLocaleString("ru-RU")}
          </span>
        )}
      </div>

      {error && <div className="vs-error vs-error-inline">{error}</div>}

      <div className="vs-card">
        {rows === null ? <div className="vs-muted vs-small">Загружаем…</div>
          : rows.length === 0 ? (
            <div className="vs-muted vs-small">За этот месяц записей нет.</div>
          ) : (
            <div className="vs-table-scroll">
            <table className="vs-table vs-rvd-table">
              <thead>
                <tr>
                  <th>Сотрудник</th><th>Дата</th><th>Часы</th><th>Компенсация</th><th>Задача</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.employee_name}<div className="vs-muted vs-small">{row.employee_number}</div></td>
                    <td className="vs-nowrap">{fmt(row.date_from)} <span className="vs-muted">{weekdayName(row.date_from)}</span></td>
                    <td>{Number(row.work_hours).toLocaleString("ru-RU")}</td>
                    <td>{row.compensation_display}</td>
                    <td className="vs-rvd-link">
                      {row.task_link
                        ? <a href={row.task_link} target="_blank" rel="noreferrer">{row.task_link.replace(/^https?:\/\//, "")}</a>
                        : <span className="vs-muted">—</span>}
                    </td>
                    <td>
                      <button className="vs-x" onClick={() => remove(row)}
                        aria-label={`Удалить РВД ${row.employee_name} ${fmt(row.date_from)}`}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
      </div>
    </div>
  );
}

const EMPTY = { date_from: "", work_hours: "8", rvd_compensation_type: "pay_plus_day_off" as RvdCompensation, task_link: "" };

function RvdForm({ onSaved }: { onSaved: (year: number, month: number) => void }) {
  const [person, setPerson] = useState<EmployeeBrief | null>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<EmployeeBrief[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const year = form.date_from ? Number(form.date_from.slice(0, 4)) : null;

  useEffect(() => {
    if (!year) return;
    api.holidays(year).then(setHolidays).catch(() => setHolidays([]));
  }, [year]);

  useEffect(() => {
    if (query.trim().length < 2) { setFound([]); return; }
    const timer = window.setTimeout(() => {
      tagsApi.searchEmployees(query.trim()).then(setFound).catch(() => setFound([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function save() {
    if (!person) return;
    setBusy(true); setErrors({}); setDone(null);
    try {
      const data: RvdInput = { employee: person.id, ...form };
      const row = await rvdApi.create(data);
      setDone(`${row.employee_name}: ${fmt(row.date_from)}, ${Number(row.work_hours)} ч — внесено.`);
      // Сотрудника оставляем: обычно вносят несколько выходных одного человека подряд.
      setForm((f) => ({ ...EMPTY, work_hours: f.work_hours, rvd_compensation_type: f.rvd_compensation_type }));
      const [y, m] = row.date_from.split("-").map(Number);
      onSaved(y, m - 1);
    } catch (err) {
      const fields = fieldErrors(err);
      setErrors(Object.keys(fields).length ? fields
        : { non_field_errors: err instanceof Error ? err.message : "Не удалось сохранить" });
    } finally {
      setBusy(false);
    }
  }

  const weekday = /^\d{4}-\d{2}-\d{2}$/.test(form.date_from) && !isRestDay(form.date_from, holidays);

  return (
    <section className="vs-card vs-rvd-form">
      <h2 className="vs-section-title">Внести</h2>
      <div className="vs-timeoff-form">
        <div className="vs-field vs-audit-person">
          Сотрудник
          {person ? (
            <span className="vs-chip-picked">
              {person.full_name}
              <button className="vs-link" aria-label="Выбрать другого сотрудника"
                onClick={() => { setPerson(null); setDone(null); }}>✕</button>
            </span>
          ) : (
            <>
              <input value={query} placeholder="ФИО или табельный" autoFocus
                onChange={(e) => setQuery(e.target.value)} />
              {found.length > 0 && (
                <ul className="vs-list vs-found vs-audit-found">
                  {found.slice(0, 8).map((e) => (
                    <li key={e.id}>
                      <button className="vs-item vs-found-item" onClick={() => {
                        setPerson(e); setQuery(""); setFound([]);
                      }}>
                        <span>{e.full_name}</span>
                        <span className="vs-muted vs-small">{e.employee_number}{e.org_unit ? ` · ${e.org_unit}` : ""}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          {errors.employee && <span className="vs-field-error">{errors.employee}</span>}
        </div>
        <label className="vs-field">
          Дата
          <input type="date" value={form.date_from}
            onChange={(e) => setForm((f) => ({ ...f, date_from: e.target.value }))} />
          {errors.date_from && <span className="vs-field-error">{errors.date_from}</span>}
        </label>
        <label className="vs-field vs-rvd-hours">
          Часов
          <input type="number" min="0.5" max="24" step="0.5" value={form.work_hours}
            onChange={(e) => setForm((f) => ({ ...f, work_hours: e.target.value }))} />
          {errors.work_hours && <span className="vs-field-error">{errors.work_hours}</span>}
        </label>
        <label className="vs-field">
          Компенсация
          <select value={form.rvd_compensation_type}
            onChange={(e) => setForm((f) => ({ ...f, rvd_compensation_type: e.target.value as RvdCompensation }))}>
            <option value="pay_plus_day_off">Оплата + отгул</option>
            <option value="double_pay">Двойная оплата</option>
          </select>
        </label>
        <label className="vs-field vs-rvd-task">
          Ссылка на задачу
          <input value={form.task_link} placeholder="https://tracker/TASK-1"
            onChange={(e) => setForm((f) => ({ ...f, task_link: e.target.value }))} />
          {errors.task_link && <span className="vs-field-error">{errors.task_link}</span>}
        </label>
        <button className="vs-btn primary" disabled={!person || !form.date_from || busy} onClick={save}>
          {busy ? "Сохраняем…" : "Внести"}
        </button>
      </div>
      {weekday && (
        <div className="vs-note">
          {fmt(form.date_from)} — рабочий день по производственному календарю.
          РВД — работа в выходной или праздник; проверьте дату.
        </div>
      )}
      {errors.non_field_errors && <div className="vs-error vs-error-inline">{errors.non_field_errors}</div>}
      {done && <div className="vs-success">{done}</div>}
    </section>
  );
}
