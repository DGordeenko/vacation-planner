import { useCallback, useEffect, useState } from "react";
import { managerApi } from "../api";
import { fmt, weekdayName } from "../dates";
import type {
  ApprovalDecision, DayOffDecision, Decisions as DecisionsData, ExceptionDecision,
} from "../types";

/** «Требует моего решения»: графики на согласовании и запросы исключений.
    Что попадает в список, решает сервер — здесь только показ и действия. */
export default function Decisions({ onBack, onChanged }: {
  onBack: () => void;
  onChanged: () => void;
}) {
  const [data, setData] = useState<DecisionsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setData(await managerApi.decisions());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить список");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  async function afterDecision(message: string) {
    setDone(message);
    await reload();
    onChanged();
  }

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Требует моего решения</h1>
      </div>

      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {done && (
        <div className="vs-success">
          {done}
          <button className="vs-link" onClick={() => setDone(null)}>скрыть</button>
        </div>
      )}

      {!data ? <div className="vs-muted">Загружаем…</div> : data.count === 0 ? (
        <div className="vs-card vs-empty">
          Решений не ждёт ничего. Новые графики и запросы появятся здесь
          и в счётчике в шапке.
        </div>
      ) : (
        <div className="vs-decisions">
          {data.exceptions.length > 0 && (
            <section>
              <h2 className="vs-section-title">
                Запросы на исключение <span className="vs-muted">{data.exceptions.length}</span>
              </h2>
              {data.exceptions.map((item) => (
                <ExceptionCard key={item.id} item={item} onDone={afterDecision} />
              ))}
            </section>
          )}
          {data.day_offs.length > 0 && (
            <section>
              <h2 className="vs-section-title">
                Отгулы <span className="vs-muted">{data.day_offs.length}</span>
              </h2>
              {data.day_offs.map((item) => (
                <DayOffCard key={item.id} item={item} onDone={afterDecision} />
              ))}
            </section>
          )}
          {data.approvals.length > 0 && (
            <section>
              <h2 className="vs-section-title">
                Графики на согласовании <span className="vs-muted">{data.approvals.length}</span>
              </h2>
              {data.approvals.map((item) => (
                <ApprovalCard key={item.step_id} item={item} onDone={afterDecision} />
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function period(from: string, to: string) {
  return from === to ? fmt(from) : `${fmt(from)} — ${fmt(to)}`;
}

function DecisionFoot({ busy, comment, setComment, commentHint, approveLabel, rejectLabel,
  requireCommentToApprove, onApprove, onReject, error }: {
  busy: boolean;
  comment: string;
  setComment: (value: string) => void;
  commentHint: string;
  approveLabel: string;
  rejectLabel: string;
  requireCommentToApprove: boolean;
  onApprove: () => void;
  onReject: () => void;
  error: string | null;
}) {
  const hasComment = comment.trim().length > 0;
  return (
    <div className="vs-decision-foot">
      <label className="vs-field">
        Комментарий
        <textarea value={comment} rows={2} placeholder={commentHint}
          onChange={(e) => setComment(e.target.value)} />
      </label>
      {error && <div className="vs-issue hard"><div className="vs-issue-text">{error}</div></div>}
      <div className="vs-decision-actions">
        <button className="vs-btn danger" onClick={onReject} disabled={busy || !hasComment}
          title={hasComment ? "" : "При отказе комментарий обязателен"}>
          {rejectLabel}
        </button>
        <button className="vs-btn primary" onClick={onApprove}
          disabled={busy || (requireCommentToApprove && !hasComment)}
          title={requireCommentToApprove && !hasComment
            ? "При подтверждении исключения комментарий обязателен" : ""}>
          {approveLabel}
        </button>
      </div>
    </div>
  );
}

function ApprovalCard({ item, onDone }: {
  item: ApprovalDecision;
  onDone: (message: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (approve) await managerApi.approveStep(item.approval_id, item.step_id, comment);
      else await managerApi.rejectStep(item.approval_id, item.step_id, comment);
      onDone(`${item.employee_name}: график ${approve ? "согласован" : "отклонён"}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить решение");
      setBusy(false);
    }
  }

  const myOrder = item.steps.find((s) => s.id === item.step_id)?.order ?? 0;
  return (
    <article className="vs-card vs-decision">
      <header className="vs-decision-head">
        <strong>{item.employee_name}</strong>
        <span className="vs-muted">
          {item.scope === "amendment" ? "Изменение утверждённого графика" : "График"} на {item.year} год
          · {item.planned_days} дн.
        </span>
      </header>
      <ul className="vs-decision-periods">
        {item.entries.map((entry) => (
          <li key={entry.id}>
            <span>{period(entry.date_from, entry.date_to)}</span>
            <span className="vs-muted">{entry.vacation_days} дн.</span>
          </li>
        ))}
      </ul>
      <ol className="vs-chain vs-chain-compact">
        {item.steps.map((step) => (
          <li key={step.id} className={`vs-chain-step ${step.status}`}>
            <span className="vs-chain-mark" aria-hidden="true">
              {step.status === "approved" ? "✓" : step.status === "rejected" ? "✕" : step.order}
            </span>
            <span className="vs-chain-body">
              <span className="vs-chain-name">
                {step.order === myOrder ? "Вы" : step.approver_name}
              </span>
              <span className="vs-chain-level">{step.level_display}</span>
            </span>
          </li>
        ))}
      </ol>
      <DecisionFoot busy={busy} comment={comment} setComment={setComment}
        commentHint="Необязателен при согласовании, обязателен при отказе"
        approveLabel="Согласовать" rejectLabel="Отклонить" requireCommentToApprove={false}
        onApprove={() => decide(true)} onReject={() => decide(false)} error={error} />
    </article>
  );
}

function ExceptionCard({ item, onDone }: {
  item: ExceptionDecision;
  onDone: (message: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      // Одно решение руководителя закрывает все нарушения этого отпуска.
      for (const violation of item.violations) {
        await managerApi.approveException(item.id, violation.code, comment);
      }
      onDone(`${item.employee_name}: исключение подтверждено.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подтвердить");
      setBusy(false);
    }
  }

  async function reject() {
    setBusy(true);
    setError(null);
    try {
      await managerApi.rejectException(item.id, comment);
      onDone(`${item.employee_name}: отпуск ${period(item.date_from, item.date_to)} не подтверждён.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отказать");
      setBusy(false);
    }
  }

  return (
    <article className="vs-card vs-decision">
      <header className="vs-decision-head">
        <strong>{item.employee_name}</strong>
        <span className="vs-muted">
          отпуск {period(item.date_from, item.date_to)} · {item.vacation_days} дн.
        </span>
      </header>
      {item.violations.map((violation) => (
        <div key={violation.code} className="vs-issue">
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
        </div>
      ))}
      <DecisionFoot busy={busy} comment={comment} setComment={setComment}
        commentHint="Обязателен: почему исключение допустимо или нет"
        approveLabel="Подтвердить исключение" rejectLabel="Отказать" requireCommentToApprove
        onApprove={approve} onReject={reject} error={error} />
    </article>
  );
}

function DayOffCard({ item, onDone }: {
  item: DayOffDecision;
  onDone: (message: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (approve) await managerApi.approveDayOff(item.id);
      else await managerApi.rejectDayOff(item.id, comment);
      onDone(`${item.employee_name}: отгул ${fmt(item.date_from)} ${approve ? "согласован" : "отклонён"}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить решение");
      setBusy(false);
    }
  }

  return (
    <article className="vs-card vs-decision">
      <header className="vs-decision-head">
        <strong>{item.employee_name}</strong>
        <span className="vs-muted">отгул {fmt(item.date_from)}, {weekdayName(item.date_from)}</span>
      </header>
      <div className="vs-muted vs-small">
        На счёте {item.balance.hours} ч — хватает на {item.balance.available_days} дн.
        Часы спишутся после согласования.
      </div>
      <DecisionFoot busy={busy} comment={comment} setComment={setComment}
        commentHint="Необязателен при согласовании, обязателен при отказе"
        approveLabel="Согласовать" rejectLabel="Отклонить" requireCommentToApprove={false}
        onApprove={() => decide(true)} onReject={() => decide(false)} error={error} />
    </article>
  );
}
