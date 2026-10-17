import { useRef, useState } from "react";
import { adminApi } from "../api";
import { fmt } from "../dates";
import type { ImportReport } from "../types";

interface Props {
  year: number;
  onDone: () => void;
  onClose: () => void;
}

const SAMPLE = `date;name;day_type
2027-01-01;Новый год;holiday
2027-02-22;Предпраздничный день;pre_holiday_short`;

/** Загрузка календаря: файл → предпросмотр с разбором → применение.
    Проблемы показываются до записи, а не после. */
export default function HolidayImport({ year, onDone, onClose }: Props) {
  const [content, setContent] = useState("");
  const [replace, setReplace] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function readFile(file: File) {
    const text = await file.text();
    setContent(text);
    setReport(null);
  }

  async function preview() {
    setBusy(true); setError(null);
    try {
      setReport(await adminApi.importCsv(content, year, replace, true));
    } catch (err) {
      const payload = (err as { payload?: ImportReport }).payload;
      if (payload?.problems) setReport(payload);
      else setError(err instanceof Error ? err.message : "Не удалось разобрать файл");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true); setError(null);
    try {
      await adminApi.importCsv(content, year, replace, false);
      onDone();
      onClose();
    } catch (err) {
      const payload = (err as { payload?: ImportReport }).payload;
      if (payload?.problems) setReport(payload);
      else setError(err instanceof Error ? err.message : "Не удалось загрузить");
    } finally {
      setBusy(false);
    }
  }

  const clean = report && report.problems.length === 0;

  return (
    <div className="vs-modal" role="dialog" aria-label="Загрузка календаря">
      <div className="vs-modal-card">
        <div className="vs-panel-head">
          <strong>Загрузить календарь на {year} год</strong>
        </div>

        <div className="vs-panel-body">
          <div className="vs-field">
            Файл CSV
            <input ref={fileRef} type="file" accept=".csv,text/csv"
              onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])} />
          </div>

          <details className="vs-details">
            <summary>Ожидаемый формат</summary>
            <pre className="vs-pre">{SAMPLE}</pre>
            <p className="vs-small vs-muted">
              Колонки <code>date</code> и <code>name</code> обязательны,
              <code> day_type</code> по умолчанию «праздничный». Принимаются
              русские заголовки (Дата, Название, Тип), разделители «;» и «,»,
              кодировки UTF-8 и CP1251, даты в форматах 01.01.2027 и 2027-01-01.
            </p>
          </details>

          <div className="vs-field">
            Или вставьте содержимое
            <textarea className="vs-textarea" rows={5} value={content}
              placeholder={SAMPLE}
              onChange={(e) => { setContent(e.target.value); setReport(null); }} />
          </div>

          <label className="vs-check">
            <input type="checkbox" checked={replace}
              onChange={(e) => { setReplace(e.target.checked); setReport(null); }} />
            Заменить календарь года полностью (иначе дни добавятся к существующим)
          </label>

          {error && <div className="vs-issue"><div className="vs-issue-text">{error}</div></div>}

          {report && report.problems.length > 0 && (
            <div className="vs-issue">
              <div className="vs-issue-name">
                Файл не будет загружен — {report.problems.length} проблем
              </div>
              <ul className="vs-problems">
                {report.problems.slice(0, 12).map((problem, index) => (
                  <li key={index}>
                    {problem.row ? `строка ${problem.row}: ` : ""}
                    {problem.message}
                    {problem.value && ` — «${problem.value}»`}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report && report.warnings.length > 0 && (
            <div className="vs-note">
              {report.warnings.map((w, i) => <div key={i}>{w.message}</div>)}
            </div>
          )}

          {clean && (
            <>
              <div className="vs-ok-box">
                Разобрано {report.preview.length} дней, ошибок нет.
                {replace && " Текущий календарь года будет удалён."}
              </div>
              <ul className="vs-preview">
                {report.preview.map((row) => (
                  <li key={row.date}>
                    <span className="vs-preview-date">{fmt(row.date)}</span>
                    <span>{row.name}</span>
                    {row.day_type !== "holiday" && (
                      <span className="vs-muted vs-small">
                        {row.day_type === "pre_holiday_short"
                          ? "сокращённый" : "перенесённый"}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="vs-panel-foot">
          <button className="vs-btn" onClick={onClose}>Отмена</button>
          <button className="vs-btn" onClick={preview} disabled={busy || !content}>
            Проверить
          </button>
          <button className="vs-btn primary" onClick={apply} disabled={busy || !clean}>
            {busy ? "Загружаем…" : "Загрузить"}
          </button>
        </div>
      </div>
    </div>
  );
}
