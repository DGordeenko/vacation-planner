import { useCallback, useEffect, useState } from "react";
import { overtimeApi } from "../api";
import { fmt, MONTHS, todayIso } from "../dates";
import { errorText, fieldErrors } from "../text";
import EmployeePicker from "./EmployeePicker";
import type {
  EmployeeBrief, Me, Overtime as OvertimeRow, OvertimeCheck, OvertimeCompensation,
  OvertimeReportRow,
} from "../types";

const STATUS: Record<string, { label: string; kind: string }> = {
  pending_approval: { label: "на согласовании", kind: "pending" },
  approved: { label: "согласовано", kind: "ok" },
  rejected: { label: "отклонено", kind: "bad" },
};

export function hours(value: number) {
  return `${Number.isInteger(value) ? value : value.toFixed(2).replace(/0$/, "").replace(".", ",")} ч`;
}

/** Сверхурочная работа: ввод по факту, два уровня согласования,
    оплата или отгул. Часы, пределы и очередь решений считает сервер. */
export default function Overtime({ me, onBack, onChanged }: {
  me: Me;
  onBack: () => void;
  onChanged: () => void;
}) {
  const year = new Date().getFullYear();
  const [rows, setRows] = useState<OvertimeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [month, setMonth] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setRows((await overtimeApi.list({ year, month: month ?? undefined })).results);
    } catch (err) {
      setError(errorText(err));
    }
  }, [year, month]);

  useEffect(() => { void load(); }, [load]);

  async function cancel(row: OvertimeRow) {
    if (!window.confirm(`Отменить сверхурочные ${fmt(row.date)} ${row.time_from}–${row.time_to}?`)) return;
    try {
      await overtimeApi.cancel(row.id);
      await load(); onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const others = me.is_manager || me.is_admin || me.is_hr;
  const ownHours = (rows ?? []).filter((r) => r.employee === me.id && r.status !== "rejected")
    .reduce((sum, r) => sum + r.hours, 0);

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Сверхурочные</h1>
      </div>

      <div className="vs-timeoff vs-overtime">
        <OvertimeForm me={me} onSaved={() => { void load(); onChanged(); }} />

        <section className="vs-card vs-timeoff-card">
          <div className="vs-section-head">
            <h2 className="vs-section-title">
              {others ? "Записи" : "Мои сверхурочные"} за {year}
            </h2>
            <label className="vs-field vs-inline-field">
              Месяц
              <select value={month ?? ""}
                onChange={(e) => setMonth(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Весь год</option>
                {MONTHS.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
              </select>
            </label>
          </div>
          {others && (
            <p className="vs-muted vs-small">
              {me.is_admin || me.is_hr ? "Показаны записи всех сотрудников." : "Показаны ваши записи и записи вашей структуры."}
            </p>
          )}
          {!others && rows && rows.length > 0 && (
            <p className="vs-muted vs-small">
              Учтено {hours(ownHours)} — согласованные и ждущие согласования.
            </p>
          )}
          {error && <div className="vs-error vs-error-inline">{error}</div>}
          {rows === null ? <div className="vs-muted">Загружаем…</div> : rows.length === 0 ? (
            <p className="vs-muted vs-small">Записей нет.</p>
          ) : (
            <div className="vs-table-scroll">
              <table className="vs-table">
                <tbody>
                  {rows.map((row) => {
                    const st = STATUS[row.status] ?? { label: row.status, kind: "" };
                    const mayCancel = me.is_admin || (row.status !== "approved"
                      && (row.employee === me.id || row.created_by === me.id));
                    return (
                      <tr key={row.id}>
                        {others && (
                          <td>{row.employee_name}
                            {row.org_unit && <div className="vs-muted vs-small">{row.org_unit}</div>}
                          </td>
                        )}
                        <td>{fmt(row.date)}
                          <div className="vs-muted vs-small">{row.time_from}–{row.time_to}, {hours(row.hours)}</div>
                        </td>
                        <td>{row.compensation_display}
                          <div className="vs-small">
                            <a href={row.task_link} target="_blank" rel="noreferrer">задача</a>
                          </div>
                        </td>
                        <td>
                          <span className={`vs-status ${st.kind}`}>{st.label}</span>
                          {row.waiting_for && (
                            <div className="vs-muted vs-small">решает {row.waiting_for}</div>
                          )}
                          {row.rejection_comment && (
                            <div className="vs-muted vs-small">«{row.rejection_comment}»</div>
                          )}
                        </td>
                        <td className="vs-override-cell">
                          {mayCancel && (
                            <button className="vs-link" onClick={() => void cancel(row)}>Отменить</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {(me.is_admin || me.is_hr) && <PayReport year={year} />}
      </div>
    </div>
  );
}

const EMPTY = { date: "", time_from: "", time_to: "", task_link: "",
  compensation: "pay" as OvertimeCompensation };

function OvertimeForm({ me, onSaved }: { me: Me; onSaved: () => void }) {
  const [form, setForm] = useState(EMPTY);
  const [forWhom, setForWhom] = useState<EmployeeBrief | null>(null);
  const [preview, setPreview] = useState<OvertimeCheck | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canForOthers = me.is_manager || me.is_admin;
  const input = { ...form, ...(forWhom ? { employee: forWhom.id } : {}) };
  const complete = Boolean(form.date && form.time_from && form.time_to);

  // Часы и предупреждения о пределах — сразу, до сохранения.
  useEffect(() => {
    setPreview(null);
    if (!complete) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      overtimeApi.check(input)
        .then((r) => { if (alive) setPreview(r); })
        .catch(() => { /* ошибки полей покажет сохранение */ });
    }, 250);
    return () => { alive = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.date, form.time_from, form.time_to, forWhom?.id, complete]);

  function set<K extends keyof typeof EMPTY>(key: K, value: (typeof EMPTY)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setDone(null);
  }

  async function save() {
    setBusy(true); setErrors({}); setError(null); setDone(null);
    try {
      const saved = await overtimeApi.create(input);
      const what = forWhom ? `${forWhom.full_name}: сверхурочные` : "Сверхурочные";
      setDone(saved.status === "approved"
        ? `${what} ${fmt(saved.date)} внесены и согласованы.`
        : `${what} ${fmt(saved.date)} отправлены на согласование — решает ${saved.waiting_for}.`);
      setForm(EMPTY); setForWhom(null);
      onSaved();
    } catch (err) {
      const fields = fieldErrors(err);
      setErrors(fields);
      if (!Object.keys(fields).length) setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const blocked = preview !== null && preview.errors.length > 0;

  return (
    <section className="vs-card vs-timeoff-card">
      <h2 className="vs-section-title">Внести сверхурочные</h2>
      <p className="vs-muted vs-small">
        Вносятся по факту. Согласуют два уровня руководства. «Оплата» уходит
        в отчёт для расчёта зарплаты, «отгул» — часы на счёт отгулов после согласования.
      </p>
      {canForOthers && (
        <EmployeePicker label="За сотрудника (пусто — за себя)" selected={forWhom}
          onSelect={setForWhom} />
      )}
      <div className="vs-timeoff-form">
        <label className="vs-field">
          Дата
          <input type="date" value={form.date} max={todayIso()}
            onChange={(e) => set("date", e.target.value)} aria-invalid={Boolean(errors.date)} />
          {errors.date && <span className="vs-field-error">{errors.date}</span>}
        </label>
        <label className="vs-field">
          С
          <input type="time" value={form.time_from} step={900}
            onChange={(e) => set("time_from", e.target.value)} />
        </label>
        <label className="vs-field">
          До
          <input type="time" value={form.time_to} step={900}
            onChange={(e) => set("time_to", e.target.value)} />
        </label>
        <label className="vs-field">
          Компенсация
          <select value={form.compensation}
            onChange={(e) => set("compensation", e.target.value as OvertimeCompensation)}>
            <option value="pay">Оплата по законодательству</option>
            <option value="day_off">Отгул</option>
          </select>
        </label>
      </div>
      <label className="vs-field">
        Ссылка на задачу
        <input type="url" value={form.task_link} placeholder="https://tracker/TASK-1"
          onChange={(e) => set("task_link", e.target.value)}
          aria-invalid={Boolean(errors.task_link)} />
        {errors.task_link && <span className="vs-field-error">{errors.task_link}</span>}
      </label>

      {preview && !blocked && (
        <div className="vs-muted vs-small">Получится {hours(preview.hours)}.</div>
      )}
      {preview && preview.errors.length > 0 && (
        <div className="vs-issue hard" role="alert">
          {preview.errors.map((text) => <div key={text} className="vs-issue-text">{text}</div>)}
        </div>
      )}
      {preview && preview.warnings.length > 0 && (
        <div className="vs-issue" role="status">
          {preview.warnings.map((text) => <div key={text} className="vs-issue-text">{text}</div>)}
          <div className="vs-muted vs-small">Сохранить можно — решение за руководителем.</div>
        </div>
      )}
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {done && <div className="vs-success">{done}</div>}

      <div>
        <button className="vs-btn primary"
          disabled={busy || !complete || !form.task_link.trim() || blocked}
          onClick={() => void save()}>
          {busy ? "Сохраняем…" : "Внести"}
        </button>
      </div>
    </section>
  );
}

/** Отчёт к оплате: только поля для расчёта зарплаты. */
function PayReport({ year }: { year: number }) {
  const [month, setMonth] = useState<number | null>(new Date().getMonth() + 1);
  const [rows, setRows] = useState<OvertimeReportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(null);
    overtimeApi.report(year, month).then(setRows).catch((err) => setError(errorText(err)));
  }, [year, month]);

  async function download() {
    try {
      const blob = await overtimeApi.reportCsv(year, month);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `сверхурочные-${year}${month ? `-${String(month).padStart(2, "0")}` : ""}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="vs-card vs-timeoff-card vs-overtime-report">
      <div className="vs-section-head">
        <h2 className="vs-section-title">К оплате</h2>
        <label className="vs-field vs-inline-field">
          Месяц
          <select value={month ?? ""}
            onChange={(e) => setMonth(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Весь год</option>
            {MONTHS.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
          </select>
        </label>
      </div>
      <p className="vs-muted vs-small">
        Согласованные сверхурочные с компенсацией «оплата». В файл уходят только
        ФИО, табельный номер, дата, тип дня и период работы.
      </p>
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {rows === null ? <div className="vs-muted">Загружаем…</div> : rows.length === 0 ? (
        <p className="vs-muted vs-small">За этот период к оплате ничего нет.</p>
      ) : (
        <>
          <div className="vs-table-scroll">
            <table className="vs-table">
              <thead>
                <tr><th>ФИО</th><th>Таб. №</th><th>Дата</th><th>Тип дня</th><th>Период</th></tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.full_name}</td><td>{r.employee_number}</td><td>{fmt(r.date)}</td>
                    <td>{r.day_type}</td><td>{r.period}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div><button className="vs-btn" onClick={() => void download()}>Скачать CSV</button></div>
        </>
      )}
    </section>
  );
}
