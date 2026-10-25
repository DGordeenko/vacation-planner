import { useCallback, useEffect, useMemo, useState } from "react";
import { managerApi, tagsApi } from "../api";
import { fmt, iso, MONTHS } from "../dates";
import type {
  TagRow, TeamCalendar as TeamCalendarData, TeamCalendarEntry, TeamScope,
} from "../types";
import TeamEntryCard from "./TeamEntryCard";

const STATUS_LABEL: Record<string, string> = {
  approved: "утверждён",
  pending_approval: "на согласовании",
  draft: "черновик",
};

/** Сводный календарь команды (UX 2.3): строки — сотрудники, столбцы — дни месяца.
    Дни, где лимит по тегу исчерпан, приходят с сервера — клиент их не считает.
    По умолчанию — своя структура; график всех подразделений руководитель
    включает сам, кого и что показать в этом режиме, решает сервер. */
export default function TeamCalendar({ year, onBack }: { year: number; onBack: () => void }) {
  const [month, setMonth] = useState(0);
  const [tags, setTags] = useState<TagRow[]>([]);
  const [tagId, setTagId] = useState<string | null>(null);
  const [onlyAbsent, setOnlyAbsent] = useState(false);
  const [data, setData] = useState<TeamCalendarData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<TeamScope>("mine");
  const [openEntry, setOpenEntry] = useState<string | null>(null);

  const days = useMemo(() => {
    const total = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    return Array.from({ length: total }, (_, i) => new Date(Date.UTC(year, month, i + 1)));
  }, [year, month]);

  useEffect(() => {
    tagsApi.list()
      .then((list) => setTags(list.filter((t) => t.members_count > 0)))
      .catch(() => setTags([]));
  }, []);

  const reload = useCallback(async () => {
    try {
      setData(await managerApi.teamCalendar(
        iso(days[0]), iso(days[days.length - 1]), tagId, scope));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить календарь");
    }
  }, [days, tagId, scope]);

  useEffect(() => { void reload(); }, [reload]);

  const limitDays = useMemo(
    () => new Map((data?.limit_reached ?? []).map((d) => [d.date, d.count])), [data]);

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    return onlyAbsent ? all.filter((row) => row.entries.length > 0) : all;
  }, [data, onlyAbsent]);

  const limited = tags.filter((t) => t.max_simultaneous_absence !== null);
  const others = tags.filter((t) => t.max_simultaneous_absence === null);

  function entryAt(entries: TeamCalendarEntry[], key: string) {
    return entries.find((e) => key >= e.date_from && key <= e.date_to);
  }

  function hint(name: string, entry: TeamCalendarEntry) {
    const what = entry.record_type === "day_off" ? "отгул" : "отпуск";
    let text = `${name}: ${what} ${fmt(entry.date_from)} — ${fmt(entry.date_to)}, `
      + (STATUS_LABEL[entry.status] ?? entry.status);
    if (entry.waiting_for) {
      text += entry.status === "draft"
        ? `, ждёт подтверждения исключения: ${entry.waiting_for}`
        : `, ждёт решения: ${entry.waiting_for}`;
    }
    return `${text}. Нажмите, чтобы открыть`;
  }

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Календарь команды</h1>
      </div>

      {data?.can_view_all && (
        <div className="vs-seg" role="group" aria-label="Чей график показать">
          <button className={scope === "mine" ? "active" : ""} aria-pressed={scope === "mine"}
            onClick={() => setScope("mine")}>Моя структура</button>
          <button className={scope === "all" ? "active" : ""} aria-pressed={scope === "all"}
            onClick={() => setScope("all")}>Все подразделения</button>
        </div>
      )}

      <div className="vs-team-controls">
        <div className="vs-month-switch">
          <button className="vs-btn" onClick={() => setMonth((m) => Math.max(0, m - 1))}
            disabled={month === 0} aria-label="Предыдущий месяц">←</button>
          <strong>{MONTHS[month]} {year}</strong>
          <button className="vs-btn" onClick={() => setMonth((m) => Math.min(11, m + 1))}
            disabled={month === 11} aria-label="Следующий месяц">→</button>
        </div>
        <label className="vs-field vs-inline-field">
          Тег
          <select value={tagId ?? ""} onChange={(e) => setTagId(e.target.value || null)}>
            <option value="">Все сотрудники</option>
            {limited.length > 0 && (
              <optgroup label="С лимитом">
                {limited.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} — не больше {t.max_simultaneous_absence}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="Без лимита">
              {others.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </optgroup>
          </select>
        </label>
        <label className="vs-check">
          <input type="checkbox" checked={onlyAbsent}
            onChange={(e) => setOnlyAbsent(e.target.checked)} />
          Только кто отсутствует в этом месяце
        </label>
      </div>

      {error && <div className="vs-error vs-error-inline">{error}</div>}

      {data?.scope === "all" && data.can_view_all && (
        <p className="vs-muted vs-small vs-team-note">
          Показан планируемый график всех подразделений. По чужим подразделениям
          видны отпуска на согласовании и утверждённые — черновики остаются у сотрудников.
        </p>
      )}

      {data?.tag && (
        <p className="vs-muted vs-small vs-team-note">
          {data.tag.limit === null
            ? `По тегу «${data.tag.name}» лимит не задан.`
            : data.limit_reached.length > 0
              ? `По тегу «${data.tag.name}» лимит ${data.tag.limit} исчерпан в отмеченные дни: `
                + data.limit_reached.map((d) => fmt(d.date)).join(", ") + "."
              : `По тегу «${data.tag.name}» лимит ${data.tag.limit} в этом месяце не исчерпан.`}
          {" "}Черновики места в лимите не занимают.
        </p>
      )}

      <div className="vs-team-scroll">
        <table className="vs-team">
          <thead>
            <tr>
              <th className="vs-team-name">Сотрудник</th>
              {days.map((d) => {
                const key = iso(d);
                const weekend = [0, 6].includes(d.getUTCDay());
                const full = limitDays.get(key);
                return (
                  <th key={key} className={`${weekend ? "weekend" : ""} ${full ? "full" : ""}`}
                    title={full ? `Лимит исчерпан: в отпуске ${full}` : undefined}>
                    {d.getUTCDate()}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.employee_id}>
                <th className="vs-team-name" scope="row">
                  <span>{row.full_name}</span>
                  {row.org_unit && <span className="vs-muted vs-small">{row.org_unit}</span>}
                </th>
                {days.map((d) => {
                  const key = iso(d);
                  const entry = entryAt(row.entries, key);
                  const weekend = [0, 6].includes(d.getUTCDay());
                  const full = limitDays.has(key);
                  const kind = entry
                    ? (entry.record_type === "day_off" ? "dayoff" : entry.status) : "";
                  return (
                    <td key={key}
                      className={`${weekend ? "weekend" : ""} ${full ? "full" : ""} ${kind}`
                        + (entry ? " clickable" : "")}
                      title={entry ? hint(row.full_name, entry) : undefined}
                      onClick={entry ? () => setOpenEntry(entry.id) : undefined}
                      // В порядок табуляции попадает только первый день отпуска в месяце.
                      tabIndex={entry && (key === entry.date_from || d.getUTCDate() === 1)
                        ? 0 : undefined}
                      onKeyDown={entry ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault(); setOpenEntry(entry.id);
                        }
                      } : undefined} />
                  );
                })}
              </tr>
            ))}
            {rows.length === 0 && data && (
              <tr><td className="vs-team-empty" colSpan={days.length + 1}>
                Нет сотрудников по выбранным условиям.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="vs-legend-card">
        <span><i className="vs-legend approved" />утверждён</span>
        <span><i className="vs-legend pending_approval" />на согласовании</span>
        <span><i className="vs-legend draft" />черновик</span>
        <span><i className="vs-legend dayoff" />отгул</span>
        {data?.tag?.limit != null && <span><i className="vs-legend full" />лимит исчерпан</span>}
      </div>

      {openEntry && <TeamEntryCard id={openEntry} onClose={() => setOpenEntry(null)} />}
    </div>
  );
}
