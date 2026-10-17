import { fmt } from "../dates";
import type { ScheduleEntry, ValidationResult } from "../types";

interface SelectionProps {
  selection: { from: string; to: string };
  check: ValidationResult | null;
  checking: boolean;
  saving: boolean;
  managerName: string | null;
  onCancel: () => void;
  onSave: () => void;
}

/** Карточка проверки: результат приходит с сервера, клиент ничего не считает. */
export function SelectionPanel({
  selection, check, checking, saving, managerName, onCancel, onSave,
}: SelectionProps) {
  const extension = check?.holiday_extension;
  const hardBlock = check?.violations.some((v) => !v.can_be_approved) ?? false;

  return (
    <div className="vs-panel" role="dialog" aria-label="Проверка отпуска">
      <div className="vs-panel-head">
        <strong>{fmt(selection.from)} — {fmt(selection.to)}</strong>
        {check && <span className="vs-muted">{check.vacation_days} отпускных дней</span>}
      </div>

      <div className="vs-panel-body">
        {checking && <div className="vs-muted">Проверяем…</div>}

        {check && (
          <>
            <dl className="vs-rows">
              <div className="vs-row">
                <dt>Календарных дней</dt><dd>{check.calendar_days}</dd>
              </div>
              <div className="vs-row">
                <dt>Списывается</dt><dd>{check.vacation_days}</dd>
              </div>
            </dl>

            {extension && extension.added_days > 0 && (
              <div className="vs-note">
                В период попали праздничные дни ({extension.added_days}). Отпуск
                продлевается до {fmt(extension.suggested_date_to)} — праздники
                не списываются с баланса.
              </div>
            )}

            {check.violations.map((violation) => (
              <div key={violation.code}
                className={`vs-issue${violation.can_be_approved ? "" : " hard"}`}>
                <div className="vs-issue-name">{violation.name}</div>
                <div className="vs-issue-text">{violation.message}</div>
                <div className="vs-issue-text">
                  {violation.can_be_approved
                    ? `Можно запросить подтверждение${managerName ? ` у ${managerName}` : ""}.`
                    : "Подтверждению не подлежит — выберите другой период."}
                </div>
              </div>
            ))}

            {check.is_valid && (
              <div className="vs-muted">Проверки пройдены — отпуск можно сохранить.</div>
            )}
          </>
        )}
      </div>

      <div className="vs-panel-foot">
        <button className="vs-btn" onClick={onCancel}>Отменить</button>
        <button className="vs-btn primary" onClick={onSave}
          disabled={saving || checking || hardBlock}>
          {saving ? "Сохраняем…"
            : check?.is_valid
              ? (extension && extension.added_days > 0 ? "Сохранить с продлением" : "Сохранить")
              : "Запросить подтверждение"}
        </button>
      </div>
    </div>
  );
}

interface EntryProps {
  entry: ScheduleEntry;
  onClose: () => void;
  onDelete: () => void;
}

export function ExistingEntryPanel({ entry, onClose, onDelete }: EntryProps) {
  const editable = entry.status === "draft";
  return (
    <div className="vs-panel" role="dialog" aria-label="Внесённый отпуск">
      <div className="vs-panel-head">
        <strong>{fmt(entry.date_from)} — {fmt(entry.date_to || entry.date_from)}</strong>
        {entry.vacation_days !== null && (
          <span className="vs-muted">{entry.vacation_days} отпускных дней</span>
        )}
      </div>
      <div className="vs-panel-body">
        <div className="vs-row">
          <dt>Статус</dt>
          <dd>{entry.status === "approved" ? "Согласован"
            : entry.status === "pending_approval" ? "На согласовании" : "Черновик"}</dd>
        </div>
        <p className="vs-muted vs-small">
          {editable
            ? "Чтобы изменить даты, удалите отпуск и выберите новый период."
            : "Отпуск отправлен на согласование — отзовите график, чтобы править."}
        </p>
      </div>
      <div className="vs-panel-foot">
        <button className="vs-btn" onClick={onClose}>Закрыть</button>
        {editable && <button className="vs-btn danger" onClick={onDelete}>Удалить</button>}
      </div>
    </div>
  );
}
