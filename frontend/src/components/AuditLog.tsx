import { useEffect, useState } from "react";
import { auditApi, tagsApi, type AuditFilter } from "../api";
import type { AuditChange, AuditEntityType, AuditRow, EmployeeBrief } from "../types";

const PAGE_SIZE = 50;

const SOURCE: Record<string, string> = {
  api: "интерфейс", import: "загрузка CSV", admin: "админка Django", system: "система",
  demo: "демо-данные",
};

function show(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value.split("-").reverse().join(".");  // год важен: журнал за разные годы
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function when(iso: string) {
  const d = new Date(iso);
  return `${d.toLocaleDateString("ru-RU")} ${d.toLocaleTimeString("ru-RU",
    { hour: "2-digit", minute: "2-digit" })}`;
}

/** Журнал изменений (релиз 1.2): кто, когда, что изменил. Только чтение —
    журнал, который можно править, не является доказательством. */
export default function AuditLog({ onBack }: { onBack: () => void }) {
  const [filter, setFilter] = useState<AuditFilter>({});
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [count, setCount] = useState(0);
  const [types, setTypes] = useState<AuditEntityType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [person, setPerson] = useState<EmployeeBrief | null>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<EmployeeBrief[]>([]);

  useEffect(() => { auditApi.entityTypes().then(setTypes).catch(() => setTypes([])); }, []);

  useEffect(() => {
    let alive = true;
    setRows(null);
    auditApi.list({ ...filter, employee_id: person?.id }, page)
      .then((data) => { if (alive) { setRows(data.results); setCount(data.count); setError(null); } })
      .catch((err) => alive && setError(err instanceof Error ? err.message : "Не удалось загрузить"));
    return () => { alive = false; };
  }, [filter, person, page]);

  useEffect(() => {
    if (query.trim().length < 2) { setFound([]); return; }
    const timer = window.setTimeout(() => {
      tagsApi.searchEmployees(query.trim()).then(setFound).catch(() => setFound([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  function update(patch: AuditFilter) {
    setFilter((f) => ({ ...f, ...patch }));
    setPage(1);
  }

  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Журнал изменений</h1>
      </div>

      <div className="vs-team-controls vs-audit-filters">
        <label className="vs-field vs-inline-field">
          Что менялось
          <select value={filter.entity_type ?? ""}
            onChange={(e) => update({ entity_type: e.target.value || undefined })}>
            <option value="">Всё</option>
            {types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
          </select>
        </label>
        <label className="vs-field vs-inline-field">
          Действие
          <select value={filter.action ?? ""}
            onChange={(e) => update({ action: e.target.value || undefined })}>
            <option value="">Любое</option>
            <option value="create">Создание</option>
            <option value="update">Изменение</option>
            <option value="delete">Удаление</option>
          </select>
        </label>
        <label className="vs-field vs-inline-field">
          С
          <input type="date" value={filter.since ?? ""}
            onChange={(e) => update({ since: e.target.value || undefined })} />
        </label>
        <label className="vs-field vs-inline-field">
          По
          <input type="date" value={filter.until ?? ""}
            onChange={(e) => update({ until: e.target.value || undefined })} />
        </label>
        <div className="vs-field vs-inline-field vs-audit-person">
          Чей объект
          {person ? (
            <span className="vs-chip-picked">
              {person.full_name}
              <button className="vs-link" aria-label="Сбросить сотрудника"
                onClick={() => { setPerson(null); setPage(1); }}>✕</button>
            </span>
          ) : (
            <>
              <input value={query} placeholder="ФИО или табельный"
                onChange={(e) => setQuery(e.target.value)} />
              {found.length > 0 && (
                <ul className="vs-list vs-found vs-audit-found">
                  {found.slice(0, 8).map((e) => (
                    <li key={e.id}>
                      <button className="vs-item vs-found-item" onClick={() => {
                        setPerson(e); setQuery(""); setFound([]); setPage(1);
                      }}>
                        <span>{e.full_name}</span>
                        <span className="vs-muted vs-small">{e.org_unit ?? ""}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>

      {error && <div className="vs-error vs-error-inline">{error}</div>}

      <div className="vs-card vs-audit">
        <div className="vs-muted vs-small vs-audit-count">
          {rows === null ? "Загружаем…" : `Записей: ${count}`}
        </div>
        {rows?.length === 0 && <div className="vs-muted">По этим условиям записей нет.</div>}
        {rows?.map((row) => (
          <article key={row.id} className={`vs-audit-row ${row.action}`}>
            <button className="vs-audit-line" aria-expanded={open === row.id}
              onClick={() => setOpen(open === row.id ? null : row.id)}>
              <span className="vs-audit-when">{when(row.changed_at)}</span>
              <span className={`vs-audit-action ${row.action}`}>{row.action_display}</span>
              <span className="vs-audit-what">
                <span className="vs-muted">{row.entity_type_display}:</span>{" "}
                {row.entity_label || "—"}
                {row.subject_name && row.entity_type !== "Employee"
                  && <span className="vs-muted"> · {row.subject_name}</span>}
              </span>
              <span className="vs-audit-who">
                {row.changed_by_name}
                {row.source && row.source !== "api"
                  && <span className="vs-muted vs-small"> · {SOURCE[row.source] ?? row.source}</span>}
              </span>
            </button>
            {open === row.id && <Changes action={row.action} changes={row.changes} />}
          </article>
        ))}
      </div>

      {pages > 1 && (
        <div className="vs-month-switch vs-audit-pages">
          <button className="vs-btn" disabled={page === 1}
            onClick={() => setPage((p) => p - 1)} aria-label="Назад">←</button>
          <span>Страница {page} из {pages}</span>
          <button className="vs-btn" disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)} aria-label="Дальше">→</button>
        </div>
      )}
    </div>
  );
}

function Changes({ action, changes }: { action: AuditRow["action"]; changes: AuditChange[] }) {
  if (changes.length === 0) return <div className="vs-muted vs-small vs-audit-diff">Без подробностей.</div>;
  return (
    <table className="vs-table vs-audit-diff">
      <tbody>
        {changes.map((c) => (
          <tr key={c.field}>
            <td className="vs-muted">{c.label}</td>
            {action === "update" ? (
              <>
                <td className="vs-audit-was">{show(c.was)}</td>
                <td aria-hidden="true">→</td>
                <td>{show(c.became)}</td>
              </>
            ) : (
              <td colSpan={3}>{show(action === "delete" ? c.was : c.became)}</td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
