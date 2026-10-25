import { fmt } from "../dates";
import type { ScheduleEntry, ValidationResult, Violation } from "../types";

/** Нарушение с подсказкой, что делать, и списком коллег при пересечении. */
function ViolationCard({ violation, hint }: { violation: Violation; hint: string }) {
  return (
    <div className={`vs-issue${violation.can_be_approved ? "" : " hard"}`}>
      <div className="vs-issue-name">{violation.name}</div>
      <div className="vs-issue-text">{violation.message}</div>
      {violation.colleagues.length > 0 && (
        <ul className="vs-issue-people">
          {violation.colleagues.map((person) => (
            <li key={`${person.name}-${person.date_from}`}>
              <span>{person.name}</span>
              <span className="vs-muted">{fmt(person.date_from)} — {fmt(person.date_to)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="vs-issue-text">{hint}</div>
    </div>
  );
}

function PanelClose({ onClose }: { onClose: () => void }) {
  return (
    <button className="vs-x vs-panel-close" onClick={onClose} aria-label="Закрыть">✕</button>
  );
}

interface SelectionProps {
  selection: { from: string; to: string };
  check: ValidationResult | null;
  checking: boolean;
  saving: boolean;
  saveError: string | null;
  managerName: string | null;
  onCancel: () => void;
  onSave: () => void;
}

/** Карточка проверки: результат приходит с сервера, клиент ничего не считает. */
export function SelectionPanel({
  selection, check, checking, saving, saveError, managerName, onCancel, onSave,
}: SelectionProps) {
  const extension = check?.holiday_extension;
  const hardBlock = check?.violations.some((v) => !v.can_be_approved) ?? false;
  const manager = managerName ? ` руководителю: ${managerName}` : " руководителю";

  return (
    <div className="vs-panel" role="dialog" aria-label="Проверка отпуска">
      <div className="vs-panel-head">
        <strong>{fmt(selection.from)} — {fmt(selection.to)}</strong>
        {check && <span className="vs-muted">{check.vacation_days} отпускных дней</span>}
        <PanelClose onClose={onCancel} />
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
              <ViolationCard key={violation.code} violation={violation}
                hint={violation.can_be_approved
                  ? `Отпуск сохранится черновиком, запрос на подтверждение уйдёт${manager}.`
                  : "Подтверждению не подлежит — выберите другой период."} />
            ))}

            {check.is_valid && (
              <div className="vs-muted">Проверки пройдены — отпуск можно сохранить.</div>
            )}
          </>
        )}

        {saveError && <div className="vs-issue hard"><div className="vs-issue-text">{saveError}</div></div>}
      </div>

      <div className="vs-panel-foot">
        <button className="vs-btn" onClick={onCancel}>Отменить</button>
        <button className="vs-btn primary" onClick={onSave}
          disabled={saving || checking || hardBlock}>
          {saving ? "Сохраняем…"
            : check?.is_valid
              ? (extension && extension.added_days > 0 ? "Сохранить с продлением" : "Сохранить")
              : "Сохранить и запросить подтверждение"}
        </button>
      </div>
    </div>
  );
}

interface EntryProps {
  entry: ScheduleEntry;
  managerName: string | null;
  onClose: () => void;
  onDelete: () => void;
}

export function ExistingEntryPanel({ entry, managerName, onClose, onDelete }: EntryProps) {
  const rejected = entry.status === "rejected";
  // Отклонённый отпуск в график не входит — его можно убрать из списка.
  const editable = entry.status === "draft" || rejected;
  const awaiting = entry.pending_exceptions.length > 0;
  return (
    <div className="vs-panel" role="dialog" aria-label="Внесённый отпуск">
      <div className="vs-panel-head">
        <strong>{fmt(entry.date_from)} — {fmt(entry.date_to || entry.date_from)}</strong>
        {entry.vacation_days !== null && (
          <span className="vs-muted">{entry.vacation_days} отпускных дней</span>
        )}
        <PanelClose onClose={onClose} />
      </div>
      <div className="vs-panel-body">
        <div className="vs-row">
          <dt>Статус</dt>
          <dd>{entry.status === "approved" ? "Согласован"
            : rejected ? "Не подтверждён руководителем"
            : entry.status === "pending_approval" ? "На согласовании"
              : awaiting ? "Черновик, ждёт подтверждения" : "Черновик"}</dd>
        </div>
        {entry.pending_exceptions.map((violation) => (
          <ViolationCard key={violation.code} violation={violation}
            hint={`Запрос на подтверждение отправлен${managerName ? `: ${managerName}` : " руководителю"}. Отправить график можно после его решения.`} />
        ))}
        {rejected && (
          <div className="vs-issue hard">
            <div className="vs-issue-name">Причина отказа</div>
            <div className="vs-issue-text">{entry.rejection_comment || "Не указана"}</div>
          </div>
        )}
        <p className="vs-muted vs-small">
          {rejected
            ? "Эти даты не входят в график. Удалите отпуск и выберите другой период."
            : editable
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
