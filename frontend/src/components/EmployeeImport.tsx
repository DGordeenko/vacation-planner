import { useState } from "react";
import { plural } from "../text";
import { ApiError, employeesAdminApi } from "../api";
import type { EmployeeImportReport, ImportProblem } from "../types";
import Invitations from "./Invitations";

const SAMPLE = `employee_number;full_name;email;hire_date;department;group;manager_employee_number;initial_balance_days;extra_tags
1001;Иванов Иван Иванович;ivanov@corp.ru;2015-01-01;Отдел эксплуатации;;;31.5;
1002;Петрова Анна Сергеевна;petrova@corp.ru;2018-03-12;Отдел эксплуатации;Группа мониторинга;1001;12.25;Дежурная смена`;

const COLUMN_TITLE: Record<string, string> = {
  employee_number: "Табельный номер",
  full_name: "ФИО",
  email: "Email",
  hire_date: "Дата приёма",
  termination_date: "Дата увольнения",
  initial_balance_days: "Остаток отпуска",
  manager_employee_number: "Руководитель",
  extra_tags: "Теги",
};

function toBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/** Загрузка сотрудников из кадровой выгрузки (UX 2.5): файл → проверка →
    применение. Проверку и подсчёт делает сервер пробной загрузкой с откатом,
    поэтому «Загрузить» доступна только после чистой проверки того же файла. */
export default function EmployeeImport({ onBack }: { onBack: () => void }) {
  const [fileName, setFileName] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [report, setReport] = useState<EmployeeImportReport | null>(null);
  const [done, setDone] = useState<EmployeeImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(0);

  async function pick(file: File) {
    setFileName(file.name);
    setReport(null); setDone(null); setError(null);
    const encoded = toBase64(await file.arrayBuffer());
    setContent(encoded);
    await run(encoded, true);
  }

  async function run(encoded: string, dryRun: boolean) {
    setBusy(true); setError(null);
    try {
      const result = await employeesAdminApi.importCsv(encoded, dryRun);
      if (dryRun) setReport(result);
      else { setDone(result); setReport(null); setContent(null); setLoaded((n) => n + 1); }
    } catch (err) {
      const payload = err instanceof ApiError
        ? err.payload as EmployeeImportReport | undefined : undefined;
      if (payload?.problems) { setReport(payload); setDone(null); }
      else setError(err instanceof Error ? err.message : "Не удалось разобрать файл");
    } finally {
      setBusy(false);
    }
  }

  const clean = report !== null && report.problems.length === 0;

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Сотрудники и приглашения</h1>
      </div>

      <p className="vs-muted vs-small vs-team-note">
        Загрузите кадровую выгрузку в CSV. Сначала файл проверяется целиком —
        ничего не записывается, пока проблем нет. Повторная загрузка обновляет
        сотрудников по табельному номеру; новым создаётся приглашение.
      </p>

      <div className="vs-card vs-import">
        <label className="vs-btn primary vs-file-btn">
          {fileName ? "Выбрать другой файл" : "Выбрать файл CSV"}
          <input type="file" accept=".csv,text/csv" hidden
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = "";
              if (f) void pick(f); }} />
        </label>
        {fileName && <span className="vs-muted vs-small">{fileName}</span>}
        {busy && <span className="vs-muted vs-small">Проверяем…</span>}

        <details className="vs-details">
          <summary>Формат файла</summary>
          <pre className="vs-pre">{SAMPLE}</pre>
          <p className="vs-small vs-muted">
            Обязательны табельный номер, ФИО, email и дата приёма. Подразделения —
            колонки уровней структуры (division, subdivision, department, group),
            руководитель — табельным номером. Принимаются русские заголовки,
            разделители «;» и «,», кодировки UTF-8 и CP1251, даты 01.01.2027
            и 2027-01-01. Без остатка отпуска дни насчитываются с даты приёма.
          </p>
        </details>
      </div>

      {error && <div className="vs-error vs-error-inline">{error}</div>}

      {done && (
        <div className="vs-success">
          Загружено: новых сотрудников {done.created}, обновлено {done.updated}.
          {done.created > 0 && " Новым сотрудникам созданы приглашения — список ниже."}
        </div>
      )}

      {report && report.problems.length > 0 && (
        <div className="vs-card">
          <div className="vs-issue-name vs-import-head">
            Файл не будет загружен: {report.problems.length}{" "}
            {plural(report.problems.length, "проблема", "проблемы", "проблем")}
            {" "}в {report.total_rows} {plural(report.total_rows, "строке", "строках", "строках")}.
            Исправьте выгрузку и выберите файл снова.
          </div>
          <ProblemTable rows={report.problems} />
        </div>
      )}

      {clean && (
        <div className="vs-card vs-import-ok">
          <div className="vs-ok-box">
            Ошибок нет. Будет создано сотрудников: <strong>{report.created}</strong>,
            обновлено: <strong>{report.updated}</strong>.
          </div>
          <div className="vs-decision-actions">
            <button className="vs-btn" onClick={() => { setReport(null); setContent(null);
              setFileName(null); }} disabled={busy}>Отмена</button>
            <button className="vs-btn primary" disabled={busy || !content}
              onClick={() => content && run(content, false)}>
              {busy ? "Загружаем…" : "Загрузить"}
            </button>
          </div>
        </div>
      )}

      {report && report.warnings.length > 0 && (
        <div className="vs-card">
          <div className="vs-issue-name vs-import-head">
            Предупреждения: {report.warnings.length}. Загрузке не мешают, но стоит проверить.
          </div>
          <ProblemTable rows={report.warnings} />
        </div>
      )}

      <Invitations reloadKey={loaded} />
    </div>
  );
}

function ProblemTable({ rows }: { rows: ImportProblem[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 50);
  return (
    <>
      <table className="vs-table">
        <tbody>
          {shown.map((row, index) => (
            <tr key={index}>
              <td className="vs-muted">{row.row ? `строка ${row.row}` : "файл"}</td>
              <td className="vs-muted">{COLUMN_TITLE[row.column] ?? row.column}</td>
              <td>{row.message}{row.value && <span className="vs-muted"> — «{row.value}»</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown.length && (
        <button className="vs-link" onClick={() => setAll(true)}>
          Показать все ({rows.length})
        </button>
      )}
    </>
  );
}
