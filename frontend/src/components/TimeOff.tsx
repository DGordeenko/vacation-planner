import { useCallback, useEffect, useMemo, useState } from "react";
import { timeOffApi, type AbsenceInput } from "../api";
import { fmt, todayIso } from "../dates";
import type {
  Absence, AbsenceReason, AbsenceSummary, Me, ScheduleEntry, TimeOffBalance, Violation,
} from "../types";

const DAY_OFF_STATUS: Record<string, { label: string; kind: string }> = {
  pending_approval: { label: "на согласовании", kind: "pending" },
  approved: { label: "согласован", kind: "ok" },
  rejected: { label: "отклонён", kind: "bad" },
  cancelled: { label: "отменён", kind: "bad" },
  draft: { label: "черновик", kind: "pending" },
};

function plural(n: number, one: string, few: string, many: string) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function hours(value: number) {
  return `${Number.isInteger(value) ? value : value.toFixed(1).replace(".", ",")} ч`;
}

/** Учёт времени сотрудника (релиз 1.2): отгулы за работу в выходные
    и внеплановые отсутствия. Все проверки — на сервере, экран только
    показывает их результат до сохранения. */
export default function TimeOff({ me, onBack, onChanged }: {
  me: Me;
  onBack: () => void;
  onChanged: () => void;
}) {
  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Отгулы и отсутствия</h1>
      </div>
      <div className="vs-timeoff">
        <DayOffs me={me} onChanged={onChanged} />
        <Absences me={me} onChanged={onChanged} />
      </div>
    </div>
  );
}

function Problems({ items }: { items: Violation[] }) {
  if (items.length === 0) return null;
  return (
    <div className="vs-issue hard" role="alert">
      {items.map((v) => (
        <div key={v.code} className="vs-issue-text">{v.message}</div>
      ))}
    </div>
  );
}

function DayOffs({ me, onChanged }: { me: Me; onChanged: () => void }) {
  const [balance, setBalance] = useState<TimeOffBalance | null>(null);
  const [list, setList] = useState<ScheduleEntry[] | null>(null);
  const [day, setDay] = useState("");
  const [problems, setProblems] = useState<Violation[]>([]);
  const [checked, setChecked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [b, l] = await Promise.all([timeOffApi.balance(me.id), timeOffApi.dayOffs(me.id)]);
      setBalance(b); setList(l.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить отгулы");
    }
  }, [me.id]);

  useEffect(() => { void load(); }, [load]);

  // Проверка сразу при выборе даты: сотрудник видит причину отказа
  // до нажатия кнопки, а не после.
  useEffect(() => {
    setChecked(null); setProblems([]);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
    let alive = true;
    timeOffApi.checkDayOff(day)
      .then((r) => { if (alive) { setProblems(r.violations); setChecked(day); } })
      .catch((err) => alive && setError(err instanceof Error ? err.message : "Проверка не удалась"));
    return () => { alive = false; };
  }, [day]);

  async function request() {
    setBusy(true); setError(null); setDone(null);
    try {
      await timeOffApi.requestDayOff(day);
      setDone(`Отгул на ${fmt(day)} отправлен руководителю${me.manager_name ? ` (${me.manager_name})` : ""}.`);
      setDay("");
      await load(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось оформить отгул");
    } finally {
      setBusy(false);
    }
  }

  async function cancel(entry: ScheduleEntry) {
    if (!window.confirm(`Отменить отгул на ${fmt(entry.date_from)}?`)) return;
    try {
      await timeOffApi.cancelDayOff(entry.id);
      await load(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отменить");
    }
  }

  const canRequest = checked === day && problems.length === 0 && !busy;

  return (
    <section className="vs-card vs-timeoff-card">
      <h2 className="vs-section-title">Отгулы</h2>
      {balance && (
        <div className="vs-timeoff-balance">
          <div>
            <span className="vs-big">{balance.free_days}</span>{" "}
            {plural(balance.free_days, "день", "дня", "дней")} можно попросить
          </div>
          {balance.pending_days > 0 && (
            <div className="vs-small">
              Ещё {balance.pending_days} {plural(balance.pending_days, "отгул ждёт", "отгула ждут", "отгулов ждут")} решения руководителя
            </div>
          )}
          <div className="vs-muted vs-small">
            {hours(balance.hours)} на счёте, отгул — {hours(balance.workday_hours)}
            {balance.remainder_hours > 0 && `, остаток ${hours(balance.remainder_hours)} до следующего дня`}
          </div>
        </div>
      )}
      <p className="vs-muted vs-small">
        Часы начисляются за работу в выходной с компенсацией «оплата + отгул» —
        её вносит администратор. Отгул согласует ваш руководитель.
      </p>

      <div className="vs-timeoff-form">
        <label className="vs-field">
          Дата отгула
          <input type="date" value={day} min={todayIso()}
            onChange={(e) => { setDay(e.target.value); setDone(null); }} />
        </label>
        <button className="vs-btn primary" disabled={!canRequest} onClick={request}>
          {busy ? "Отправляем…" : "Попросить отгул"}
        </button>
      </div>
      <Problems items={problems} />
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {done && <div className="vs-success">{done}</div>}

      {list && list.length > 0 && (
        <ul className="vs-list vs-timeoff-list">
          {list.map((entry) => {
            const st = DAY_OFF_STATUS[entry.status] ?? { label: entry.status, kind: "" };
            return (
              <li key={entry.id} className="vs-item">
                <span className="vs-item-dates">{fmt(entry.date_from)}</span>
                <span className={`vs-status ${st.kind}`}>{st.label}</span>
                {entry.status === "pending_approval" && (
                  <button className="vs-link" onClick={() => cancel(entry)}>Отменить</button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {balance && balance.history.length > 0 && (
        <details className="vs-details">
          <summary>История начислений и списаний</summary>
          <table className="vs-table">
            <tbody>
              {balance.history.map((row, i) => (
                <tr key={i}>
                  <td className="vs-muted">{fmt(row.date)}</td>
                  <td className={row.hours >= 0 ? "vs-plus" : "vs-minus"}>
                    {row.hours >= 0 ? "+" : "−"}{hours(Math.abs(row.hours))}
                  </td>
                  <td>{row.reason}{row.comment && <span className="vs-muted"> · {row.comment}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}

const EMPTY: AbsenceInput = { date_from: "", date_to: null, reason: null, custom_reason_text: "" };

function Absences({ me, onChanged }: { me: Me; onChanged: () => void }) {
  const year = new Date().getFullYear();
  const [reasons, setReasons] = useState<AbsenceReason[]>([]);
  const [list, setList] = useState<Absence[] | null>(null);
  const [summary, setSummary] = useState<AbsenceSummary | null>(null);
  const [form, setForm] = useState<AbsenceInput>(EMPTY);
  const [problems, setProblems] = useState<Violation[]>([]);
  const [days, setDays] = useState<number | null>(null);
  const [checkedKey, setCheckedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [l, s] = await Promise.all([
        timeOffApi.absences(me.id, year), timeOffApi.absenceSummary(me.id, year)]);
      setList(l.results); setSummary(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить отсутствия");
    }
  }, [me.id, year]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    timeOffApi.reasons().then((p) => setReasons(p.results)).catch(() => setReasons([]));
  }, []);

  const reason = reasons.find((r) => r.id === form.reason);
  const key = JSON.stringify(form);

  useEffect(() => {
    setCheckedKey(null); setProblems([]); setDays(null);
    if (!form.date_from || !form.reason) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      timeOffApi.checkAbsence(form)
        .then((r) => {
          if (!alive) return;
          setProblems(r.violations); setDays(r.days); setCheckedKey(key);
        })
        .catch((err) => alive && setError(err instanceof Error ? err.message : "Проверка не удалась"));
    }, 250);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [key, form]);

  async function save() {
    setBusy(true); setError(null); setDone(null);
    try {
      const saved = await timeOffApi.recordAbsence(form);
      setDone(`Отсутствие ${fmt(saved.date_from)}${saved.date_to && saved.date_to !== saved.date_from
        ? ` — ${fmt(saved.date_to)}` : ""} внесено.`);
      setForm(EMPTY);
      await load(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось внести");
    } finally {
      setBusy(false);
    }
  }

  async function remove(row: Absence) {
    if (!window.confirm(`Удалить отсутствие ${fmt(row.date_from)}?`)) return;
    try {
      await timeOffApi.deleteAbsence(row.id);
      await load(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    }
  }

  const ready = useMemo(() => checkedKey === key && problems.length === 0 && !busy,
    [checkedKey, key, problems, busy]);
  const today = todayIso();

  return (
    <section className="vs-card vs-timeoff-card">
      <h2 className="vs-section-title">Внеплановые отсутствия</h2>
      <p className="vs-muted vs-small">
        Больничный, отпуск за свой счёт и другие отсутствия вне графика. Вносятся по
        факту, согласования не нужно — руководитель получит уведомление. На остаток
        отпуска не влияют.
      </p>

      <div className="vs-timeoff-form vs-absence-form">
        <label className="vs-field">
          С
          <input type="date" value={form.date_from} max={today}
            onChange={(e) => { setForm((f) => ({ ...f, date_from: e.target.value })); setDone(null); }} />
        </label>
        <label className="vs-field">
          По
          <input type="date" value={form.date_to ?? ""} max={today} min={form.date_from || undefined}
            onChange={(e) => setForm((f) => ({ ...f, date_to: e.target.value || null }))} />
        </label>
        <label className="vs-field">
          Причина
          <select value={form.reason ?? ""}
            onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value || null }))}>
            <option value="">Выберите…</option>
            {reasons.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
        {reason?.is_other && (
          <label className="vs-field vs-absence-other">
            Что случилось
            <input value={form.custom_reason_text} placeholder="Коротко, для руководителя"
              onChange={(e) => setForm((f) => ({ ...f, custom_reason_text: e.target.value }))} />
          </label>
        )}
        <button className="vs-btn primary" disabled={!ready} onClick={save}>
          {busy ? "Сохраняем…" : days ? `Внести ${days} ${plural(days, "день", "дня", "дней")}` : "Внести"}
        </button>
      </div>
      <div className="vs-muted vs-small">Один день — оставьте «По» пустым.</div>
      <Problems items={problems} />
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {done && <div className="vs-success">{done}</div>}

      {summary && summary.episodes > 0 && (
        <div className="vs-muted vs-small">
          В {year} году: {summary.total_days} {plural(summary.total_days, "день", "дня", "дней")}
          {" "}({summary.by_reason.map((r) => `${r.reason.toLowerCase()} — ${r.days}`).join(", ")})
        </div>
      )}

      {list && list.length > 0 && (
        <ul className="vs-list vs-timeoff-list">
          {list.map((row) => (
            <li key={row.id} className="vs-item">
              <span className="vs-item-dates">
                {fmt(row.date_from)}{row.date_to && row.date_to !== row.date_from && ` — ${fmt(row.date_to)}`}
              </span>
              <span className="vs-item-days">{row.days} дн.</span>
              <span>
                {row.reason_name ?? "Без причины"}
                {row.custom_reason_text && <span className="vs-muted">: {row.custom_reason_text}</span>}
                {row.recorded_by && row.recorded_by_id !== me.id
                  && <span className="vs-muted vs-small"> · внёс {row.recorded_by}</span>}
              </span>
              {row.recorded_by_id === me.id && (
                <button className="vs-x" onClick={() => remove(row)}
                  aria-label={`Удалить отсутствие ${fmt(row.date_from)}`}>✕</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {list && list.length === 0 && (
        <div className="vs-muted vs-small">В {year} году отсутствий не было.</div>
      )}
    </section>
  );
}
